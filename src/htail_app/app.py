from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple
import urllib.error
import urllib.request

from . import VERSION
from . import core
from .input import InputReader, InputEvent, MouseEvent
from .fsnotify import FsEvents, NativeWatchHub
from .globwatch import DynamicGlob, has_magic
from .layout import LAYOUTS, Rect, pane_rects, resolve_auto
from .pane import Pane, StreamPane
from .searching import GlobalSearchMatch, SEARCH_REGEX, SEARCH_SIMPLE, compile_search, preview_around_match, search_label
from .sources import CommandFollower, StreamFollower
from .watcher import FileFollower, WatchNotice, WatchUpdate

# The reusable core is copied from the previous single-file implementation.
# Override its runtime version so UpdateService and all status text use the
# packaged application version rather than the legacy source snapshot.
core.HTAIL_VERSION = VERSION

INTERACTIVE_FRAME_INTERVAL = 1.0 / 60.0
MIN_UPDATE_MODAL_SECONDS = 0.60
MIN_UPDATE_COMPLETE_SECONDS = 0.35
GLOBAL_SEARCH_LIMIT = 250


def executable_path() -> Path:
    """Return the self-updatable wrapper rather than the cached package payload."""
    wrapped = os.environ.get("HTAIL_EXECUTABLE")
    if wrapped:
        return Path(wrapped).expanduser().resolve()
    return Path(sys.argv[0]).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="htail",
        description="Follow one or more text files in an interactive highlighted terminal viewer.",
    )
    parser.add_argument("--version", action="version", version=f"htail {VERSION}")
    parser.add_argument("files", type=Path, nargs="*", help="text files or quoted glob patterns to watch; use '-' for stdin")
    parser.add_argument("--glob", dest="globs", action="append", default=[], metavar="PATTERN", help="dynamically add files matching PATTERN; repeatable")
    parser.add_argument("--exec", dest="commands", action="append", default=[], metavar="COMMAND", help="run a shell command and watch its merged stdout/stderr; repeatable")
    parser.add_argument("--pid", type=int, metavar="PID", help="exit after this process is no longer running")
    parser.add_argument("--install", nargs="?", const=core.DEFAULT_INSTALL_COMMAND, metavar="NAME")
    parser.add_argument("--no-self-install-prompt", action="store_true")
    parser.add_argument("--check-update", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("-n", "--lines", type=int, default=None, help="initial source-line limit for non-interactive output; interactive mode reads the full file")
    parser.add_argument("-i", "--interval", type=float, default=0.10, help="poll interval in seconds")
    parser.add_argument("--verify-interval", type=float, default=1.0)
    parser.add_argument("--debounce", type=float, default=0.15)
    parser.add_argument("--max-debounce", type=float, default=1.0)
    parser.add_argument("--encoding", default="utf-8")
    parser.add_argument("--syntax", default="auto", metavar="LANG")
    parser.add_argument("--no-install-prompt", action="store_true")
    parser.add_argument("--show-deletions", action="store_true")
    parser.add_argument("--mark-replacements", action="store_true")
    parser.add_argument("--grep", metavar="REGEX")
    parser.add_argument("--exclude", metavar="REGEX")
    parser.add_argument("-I", "--ignore-case", action="store_true")
    parser.add_argument("--idle-warn", type=float, default=300.0, metavar="SECONDS")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--no-start-banner", action="store_true")
    parser.add_argument(
        "--layout",
        choices=LAYOUTS,
        default="auto",
        help="initial multi-file layout: auto, rows, columns, grid, or stream",
    )
    parser.add_argument(
        "--no-mouse",
        action="store_true",
        help="disable terminal mouse tracking (keyboard pane selection still works)",
    )
    parser.add_argument("--no-native-watch", action="store_true", help="disable native filesystem notifications and use polling only")
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.lines is not None and args.lines < 0:
        parser.error("--lines must be >= 0")
    if args.interval <= 0:
        parser.error("--interval must be > 0")
    if args.verify_interval < 0:
        parser.error("--verify-interval must be >= 0")
    if args.debounce < 0 or args.max_debounce < 0:
        parser.error("--debounce and --max-debounce must be >= 0")
    if args.idle_warn < 0:
        parser.error("--idle-warn must be >= 0")
    if args.pid is not None and args.pid <= 0:
        parser.error("--pid must be > 0")
    if sum(1 for path in args.files if str(path) == "-") > 1:
        parser.error("stdin ('-') can only be used once")
    return args



def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok and code.value == STILL_ACTIVE)
    except Exception:
        return True


def _install_executable(command_name: str) -> Tuple[bool, str, Path]:
    source = executable_path()
    target = Path.home() / ".local" / "bin" / command_name
    collision = core._command_collision(command_name, source)
    if collision is not None:
        return False, f"'{command_name}' already resolves to {collision}; nothing was overwritten", target
    if not source.is_file():
        return False, f"cannot locate running htail executable: {source}", target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{command_name}.install-", dir=str(target.parent))
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(source.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, source.stat().st_mode | 0o111)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    except OSError as exc:
        return False, f"could not install {command_name}: {exc}", target
    path_ok, path_message = core._ensure_local_bin_path()
    message = f"installed as {target}. {path_message}"
    if not path_ok:
        message += ". The command will not be globally available until PATH is updated."
    return True, message, target


def maybe_offer_self_install(args: argparse.Namespace, color: bool) -> None:
    source = executable_path()
    if args.install is not None:
        ok, message, _ = _install_executable(args.install or core.DEFAULT_INSTALL_COMMAND)
        print(core.paint(f"[htail] {message}", core.GREEN if ok else core.BOLD_YELLOW, color), file=sys.stdout if ok else sys.stderr)
        raise SystemExit(0 if ok else 1)
    if args.no_self_install_prompt or args.check_update or args.update:
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()) or not source.is_file():
        return
    existing = shutil.which(core.DEFAULT_INSTALL_COMMAND)
    if existing and core._same_file(Path(existing), source):
        return
    state = core._load_app_state()
    if state.get("self_install_prompted"):
        return
    proposed, collision = core.choose_install_name(source)
    state["self_install_prompted"] = True
    core._save_app_state(state)
    if proposed is None:
        print(core.paint("[htail] No free command name among ht, htail and hlog; use --install NAME.", core.BOLD_YELLOW, color))
        return
    if collision is not None:
        print(core.paint(f"[htail] 'ht' already resolves to {collision}; it will not be overwritten.", core.BOLD_YELLOW, color))
        prompt = f"Install as '{proposed}' in ~/.local/bin instead? [Y/n] "
    else:
        prompt = f"Install htail as '{proposed}' in ~/.local/bin so it is available everywhere? [Y/n] "
    print(prompt, end="", flush=True)
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer not in ("", "y", "yes"):
        print("[htail] installation skipped. Use --install later if needed.")
        return
    ok, message, _ = _install_executable(proposed)
    print(core.paint(f"[htail] {message}", core.GREEN if ok else core.BOLD_YELLOW, color))


def _pad(text: str, width: int) -> str:
    text = core.clip_ansi(text, max(0, width))
    visible = len(core.strip_ansi(text))
    return text + " " * max(0, width - visible)


def _slice_ansi(text: str, start: int, width: int) -> str:
    plain = core.strip_ansi(text)
    if width <= 0 or start >= len(plain):
        return ""
    if start <= 0:
        clipped = core.clip_ansi(text, width)
        return clipped
    remainder = text
    visible = 0
    i = 0
    while i < len(remainder) and visible < start:
        match = core.ANSI_RE.match(remainder, i)
        if match:
            i = match.end()
            continue
        visible += 1
        i += 1
    return core.clip_ansi(remainder[i:], width)


def _dim_line(text: str, color: bool) -> str:
    if not color:
        return text
    # Embedded syntax-highlight resets cancel SGR 2 (dim). Strip the background
    # styling first, then apply one uniform dim style so a modal always reads
    # as foreground over a subdued pane rather than over fully bright colours.
    plain = core.strip_ansi(text)
    if not plain.strip():
        return plain
    return core.DIM + plain + core.RESET


def _panel_geometry(title: str, content: Sequence[str], width: int, height: int, color: bool) -> Tuple[int, int, int, int, List[str]]:
    if width < 34 or height < 5:
        rows: List[str] = []
        for line in content:
            rows.extend(core.wrap_ansi(line, width) if line else [""])
        if not color:
            rows = [core.strip_ansi(row) for row in rows]
        rows = [_pad(line, width) for line in rows[:height]] + [" " * width] * max(0, height - len(rows))
        return 0, 0, width, min(height, len(rows)), rows[:height]

    panel_width = min(90, max(34, width - 6))
    inner_width = panel_width - 4
    rendered: List[str] = []
    for line in content:
        rendered.extend(core.wrap_ansi(line, inner_width) if line else [""])
    if not color:
        rendered = [core.strip_ansi(row) for row in rendered]
    limit = max(1, height - 4)
    if len(rendered) > limit:
        rendered = rendered[: max(0, limit - 1)] + [core.paint("… more omitted", core.DIM, color)]

    label = f" {title} "
    label_visible = len(label)
    fill = max(0, panel_width - 2 - label_visible)
    left_fill = max(1, fill // 2)
    right_fill = max(1, fill - left_fill)
    if left_fill + right_fill + label_visible > panel_width - 2:
        right_fill = max(1, panel_width - 2 - label_visible - left_fill)
    top_plain = "╭" + "─" * left_fill + label + "─" * right_fill + "╮"
    top_plain = top_plain[:panel_width-1] + "╮" if len(top_plain) > panel_width else top_plain.ljust(panel_width - 1, "─") + "╮"
    bottom_plain = "╰" + "─" * (panel_width - 2) + "╯"
    if len(bottom_plain) > panel_width:
        bottom_plain = bottom_plain[:panel_width-1] + "╯"
    side_plain = "│"
    top = core.paint(top_plain, core.BOLD_LIGHT_CYAN, color)
    bottom = core.paint(bottom_plain, core.BOLD_LIGHT_CYAN, color)
    side = core.paint(side_plain, core.CYAN, color)
    rows = [_pad(top, panel_width)]
    for line in rendered:
        rows.append(_pad(side + " " + _pad(line, inner_width) + " " + side, panel_width))
    rows.append(_pad(bottom, panel_width))
    left = max(0, (width - panel_width) // 2)
    top_y = max(0, (height - len(rows)) // 2)
    return left, top_y, panel_width, len(rows), rows


def _panel_lines(title: str, content: Sequence[str], width: int, height: int, color: bool) -> List[str]:
    left, top_y, panel_width, panel_height, panel_rows = _panel_geometry(title, content, width, height, color)
    out = [" " * width for _ in range(height)]
    for i, row in enumerate(panel_rows[:height]):
        y = top_y + i
        if 0 <= y < height:
            out[y] = (" " * left) + row + (" " * max(0, width - left - len(core.strip_ansi(row))))
    if not color:
        out = [core.strip_ansi(row) for row in out]
    return out


def _changed_frame_rows(previous: Optional[Sequence[str]], current: Sequence[str]) -> List[int]:
    if previous is None or len(previous) != len(current):
        return list(range(len(current)))
    return [i for i, (old, new) in enumerate(zip(previous, current)) if old != new]


def _overlay_modal(background: Sequence[str], panel: Sequence[str], width: int, height: int, color: bool) -> List[str]:
    out: List[str] = []
    for y in range(height):
        bg = background[y] if y < len(background) else (" " * width)
        fg = panel[y] if y < len(panel) else (" " * width)
        if not core.strip_ansi(fg).strip():
            out.append(_dim_line(bg, color))
            continue
        plain_fg = core.strip_ansi(fg)
        non_space = [i for i, ch in enumerate(plain_fg) if ch != ' ']
        if not non_space:
            out.append(_dim_line(bg, color))
            continue
        start = non_space[0]
        end = non_space[-1] + 1
        left = _slice_ansi(bg, 0, start)
        mid = _slice_ansi(fg, start, end - start)
        right = _slice_ansi(bg, end, max(0, width - end))
        out.append(_dim_line(left, color) + mid + _dim_line(right, color))
    return out


class MultiApp:
    def __init__(self, args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter, update_service: core.UpdateService) -> None:
        self.args = args
        self.color = color
        self.display_filter = display_filter
        self.update_service = update_service
        raw_paths = list(args.files)
        self.glob_trackers = [
            DynamicGlob(str(path))
            for path in raw_paths
            if str(path) != "-" and has_magic(str(path))
        ]
        self.glob_trackers.extend(DynamicGlob(pattern) for pattern in args.globs)
        self.paths = [
            path for path in raw_paths
            if str(path) == "-" or not has_magic(str(path))
        ]
        self.native_watch = NativeWatchHub(enabled=not args.no_native_watch)
        setattr(self.args, "notification_gated", self.native_watch.available)
        self._known_file_paths = set()
        self._next_glob_scan = 0.0
        for tracker in self.glob_trackers:
            self.native_watch.add_directory(tracker.root)
            self.paths.extend(tracker.scan())

        deduped_paths: List[Path] = []
        initial_seen = set()
        for path in self.paths:
            key = ("stdin",) if str(path) == "-" else ("file", os.path.abspath(os.fspath(path)))
            if key in initial_seen:
                continue
            initial_seen.add(key)
            deduped_paths.append(path)
        self.paths = deduped_paths

        self.followers: List[object] = []
        self.panes: List[Pane] = []
        self.stream = StreamPane(color, args.idle_warn)
        self.layout = args.layout
        self.focus = 0
        self.maximized = False
        self.layout_menu = False
        self.help_active = False
        self.update_confirm_active = False
        self.update_installing = False
        self.update_install_status = ""
        self.update_install_progress: Optional[Tuple[int, Optional[int]]] = None
        self.update_overall_progress = 0.0
        self.update_progress_started_at: Optional[float] = None
        self.update_install_result: Optional[Tuple[bool, str]] = None
        self.update_release: Optional[core.ReleaseInfo] = None
        self.pending_restart: Optional[Tuple[Path, List[str], str]] = None
        self.pending_restart_at: Optional[float] = None
        self.update_check_done = False
        self.update_check_error: Optional[str] = None
        self.update_manual_check_pending = False
        self.last_update_check_monotonic = time.monotonic()
        self.message: Optional[str] = None
        self.message_until = 0.0
        self.last_status_second: Optional[int] = None
        self.last_rects: List[Tuple[int, Rect]] = []
        self.dirty = True
        self.prompt_mode: Optional[str] = None
        self.prompt_buffer = ""
        self.prompt_search_mode = SEARCH_SIMPLE
        self.global_search_active = False
        self.global_search_buffer = ""
        self.global_search_mode = SEARCH_SIMPLE
        self.global_search_results: List[GlobalSearchMatch] = []
        self.global_search_selected = 0
        self.global_search_error: Optional[str] = None
        self.global_search_truncated = False
        self._last_frame: Optional[List[str]] = None
        self._last_frame_geometry: Optional[Tuple[int, int]] = None
        self.render_rows_written = 0
        self.render_frames = 0

        for path in self.paths:
            if str(path) == "-":
                pseudo = Path("stdin.txt")
                highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)
                pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name="stdin")
                follower = StreamFollower(sys.stdin, args, label="stdin")
            else:
                highlighter = core.SyntaxHighlighter(path, args.syntax, color)
                pane = Pane(path, highlighter, display_filter, color, args.idle_warn)
                follower = FileFollower(path, args)
            notice = follower.initialize_if_available()
            if notice and notice.initial_tail is not None:
                pane.add_initial(notice.initial_tail)
                pane.set_snapshot(follower.previous)
                if notice.initial_tail:
                    self._stream_initial(len(self.panes), pane, notice.initial_tail)
            else:
                pane.waiting = True
                if notice and notice.kind == "error":
                    pane.add_system_line(notice.text, warning=True)
            if highlighter.warning:
                pane.add_system_line(highlighter.warning, warning=True)
            self.panes.append(pane)
            self.followers.append(follower)
            if str(path) != "-":
                self._known_file_paths.add(Path(os.path.abspath(os.fspath(path))))
                self.native_watch.add_file(path)

        for command_index, command in enumerate(args.commands, start=1):
            pseudo = Path(f"command-{command_index}.log")
            highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)
            label = f"$ {command}"
            pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name=label)
            follower = CommandFollower(command, args, label=label)
            follower.initialize_if_available()
            pane.set_message(f"running pid {follower.process.pid}", 4.0)
            self.panes.append(pane)
            self.followers.append(follower)

    def _add_dynamic_file(self, path: Path) -> bool:
        normalized = Path(os.path.abspath(os.fspath(path)))
        if normalized in self._known_file_paths:
            return False
        highlighter = core.SyntaxHighlighter(path, self.args.syntax, self.color)
        pane = Pane(path, highlighter, self.display_filter, self.color, self.args.idle_warn)
        follower = FileFollower(path, self.args)
        notice = follower.initialize_if_available()
        if notice and notice.initial_tail is not None:
            pane.add_initial(notice.initial_tail)
            pane.set_snapshot(follower.previous)
            if notice.initial_tail:
                self._stream_initial(len(self.panes), pane, notice.initial_tail)
        else:
            pane.waiting = True
            if notice and notice.kind == "error":
                pane.add_system_line(notice.text, warning=True)
        if highlighter.warning:
            pane.add_system_line(highlighter.warning, warning=True)
        self.paths.append(path)
        self.panes.append(pane)
        self.followers.append(follower)
        self._known_file_paths.add(normalized)
        self.native_watch.add_file(path)
        self.set_message(f"glob added {pane.name}", 4.0)
        return True

    def _refresh_globs(self, now: float, events: Optional[FsEvents] = None) -> None:
        if not self.glob_trackers:
            return
        event_wakeup = bool(events and (events.paths or events.directories))
        if not event_wakeup and now < self._next_glob_scan:
            return
        self._next_glob_scan = now + 2.0
        for tracker in self.glob_trackers:
            self.native_watch.add_directory(tracker.root)
            for path in tracker.scan():
                self._add_dynamic_file(path)

    def close_native_watch(self) -> None:
        self.native_watch.close()

    def _stream_initial(self, index: int, pane: Pane, raw_lines: Sequence[str]) -> None:
        visible = [line for line in raw_lines if self.display_filter.accepts(line)]
        rendered = core.render_initial_lines(visible, pane.highlighter)
        if self.stream.lines:
            self.stream.lines.append("")
        self.stream.lines.append(core.paint(f"━━ [{index + 1}] {pane.name} · initial context ━━", core.DIM, self.color))
        self.stream.lines.extend(rendered)
        self.stream._mark_layout_dirty()

    def set_message(self, text: str, duration: float = 3.0) -> None:
        self.message = text
        self.message_until = time.monotonic() + duration
        self.dirty = True

    def dimensions(self) -> Tuple[int, int]:
        size = shutil.get_terminal_size((120, 32))
        return max(30, size.columns), max(6, size.lines)

    def footer_height(self, terminal_width: int) -> int:
        return 1 if terminal_width < 78 else 2

    def content_dimensions(self) -> Tuple[int, int, int]:
        terminal_width, terminal_height = self.dimensions()
        # Preserve one physical column to avoid terminal implicit-wrap state.
        width = max(1, terminal_width - 1)
        footer = self.footer_height(terminal_width)
        return width, max(1, terminal_height - footer), footer

    def resolved_layout(self, width: int, height: int) -> str:
        return resolve_auto(len(self.panes), width, height) if self.layout == "auto" else self.layout

    def active_pane(self) -> Pane:
        if self.layout == "stream":
            return self.stream
        if not self.panes:
            return self.stream
        self.focus %= len(self.panes)
        return self.panes[self.focus]

    def focus_next(self, delta: int) -> None:
        if not self.panes:
            return
        self.focus = (self.focus + delta) % len(self.panes)
        self.dirty = True

    def _pane_boxes(self, width: int, height: int) -> List[str]:
        if self.layout == "stream":
            rects = [Rect(0, 0, width, height)]
            displayed: List[Tuple[int, Pane, Rect]] = [(-1, self.stream, rects[0])]
            self.last_rects = [(-1, rects[0])]
        elif self.maximized and self.panes:
            rect = Rect(0, 0, width, height)
            displayed = [(self.focus, self.panes[self.focus], rect)]
            self.last_rects = [(self.focus, rect)]
        else:
            rects = pane_rects(self.layout, len(self.panes), width, height)
            displayed = [(i, self.panes[i], rects[i]) for i in range(min(len(self.panes), len(rects))) if rects[i].width > 0 and rects[i].height > 0]
            self.last_rects = [(i, rect) for i, _, rect in displayed]

        segments_by_row: List[List[Tuple[int, str, int]]] = [[] for _ in range(height)]
        for index, pane, rect in displayed:
            focused = index == self.focus if index >= 0 else True
            box_index = index if index >= 0 else 0
            box = pane.render_box(rect.width, rect.height, focused, box_index)
            for local_y, row in enumerate(box):
                y = rect.y + local_y
                if 0 <= y < height:
                    segments_by_row[y].append((rect.x, _pad(row, rect.width), rect.width))

        out: List[str] = []
        for segments in segments_by_row:
            segments.sort(key=lambda item: item[0])
            cursor = 0
            line = ""
            for x, segment, segment_width in segments:
                if x > cursor:
                    line += " " * (x - cursor)
                line += segment
                cursor = x + segment_width
            if cursor < width:
                line += " " * (width - cursor)
            out.append(line)
        return out

    def _search_flags(self) -> int:
        return re.IGNORECASE if self.args.ignore_case else 0

    @staticmethod
    def _other_search_mode(mode: str) -> str:
        return SEARCH_REGEX if mode == SEARCH_SIMPLE else SEARCH_SIMPLE

    @staticmethod
    def _search_mode_name(mode: str) -> str:
        return "Simple" if mode == SEARCH_SIMPLE else "Regex"

    def _prompt_lines(self, width: int, height: int) -> List[str]:
        mode = self.prompt_mode or "search"
        if mode == "search":
            mode_name = self._search_mode_name(self.prompt_search_mode)
            title = f"Search · {mode_name}"
            content = [
                core.paint("/ " + self.prompt_buffer, core.BOLD_LIGHT_CYAN, self.color),
                "",
                f"Mode: {mode_name} · Tab toggles Simple / Regex",
            ]
            if self.prompt_search_mode == SEARCH_SIMPLE:
                content.append("Simple: ordinary text is literal · * any text · ? one character")
            else:
                content.append("Regex: Python regular-expression syntax")
            content.extend(["", "Enter apply · Esc cancel · Backspace edit"])
            return _panel_lines(title, content, width, height, self.color)

        content = [
            core.paint("highlight: " + self.prompt_buffer, core.BOLD_LIGHT_CYAN, self.color),
            "",
            "Regex highlight · Enter apply · Esc cancel · Backspace edit",
            "Use H from the viewer to clear the active highlight.",
        ]
        return _panel_lines("Regex highlight", content, width, height, self.color)

    def _refresh_global_search_results(self) -> None:
        pattern, error = compile_search(self.global_search_buffer, self.global_search_mode, self._search_flags())
        self.global_search_error = error
        self.global_search_truncated = False
        if pattern is None:
            self.global_search_results = []
            self.global_search_selected = 0
            return

        results: List[GlobalSearchMatch] = []
        for pane_index, pane in enumerate(self.panes):
            for source_index, raw in enumerate(pane.snapshot_raw):
                if not pane.display_filter.accepts(raw):
                    continue
                plain = raw.rstrip("\r\n")
                match = pattern.search(plain)
                if match is None:
                    continue
                results.append(GlobalSearchMatch(
                    pane_index,
                    source_index,
                    pane.name,
                    plain,
                    match.start(),
                    match.end(),
                ))
                if len(results) >= GLOBAL_SEARCH_LIMIT:
                    self.global_search_truncated = True
                    break
            if self.global_search_truncated:
                break
        self.global_search_results = results
        if results:
            self.global_search_selected = min(max(0, self.global_search_selected), len(results) - 1)
        else:
            self.global_search_selected = 0

    def _global_search_lines(self, width: int, height: int) -> List[str]:
        self._refresh_global_search_results()
        mode_name = self._search_mode_name(self.global_search_mode)
        count = len(self.global_search_results)
        count_label = f"{count}+ matches" if self.global_search_truncated else f"{count} match{'es' if count != 1 else ''}"
        content: List[str] = [
            core.paint("> " + self.global_search_buffer, core.BOLD_LIGHT_CYAN, self.color),
            f"Mode: {mode_name} · Tab toggles Simple / Regex · {count_label}",
        ]
        if self.global_search_mode == SEARCH_SIMPLE:
            content.append("Simple: literal text · * any text · ? one character")
        else:
            content.append("Regex: Python regular-expression syntax")
        content.append("")

        if self.global_search_error:
            content.append(core.paint(f"Invalid regex: {self.global_search_error}", core.BOLD_YELLOW, self.color))
        elif not self.global_search_buffer:
            content.append(core.paint("Type to search every currently watched file.", core.DIM, self.color))
        elif not self.global_search_results:
            content.append(core.paint("No matches.", core.DIM, self.color))
        else:
            visible_slots = max(3, min(12, height - 10))
            selected = self.global_search_selected
            start = max(0, selected - visible_slots // 2)
            start = min(start, max(0, len(self.global_search_results) - visible_slots))
            end = min(len(self.global_search_results), start + visible_slots)
            preview_limit = max(18, min(80, width - 34))
            for index in range(start, end):
                result = self.global_search_results[index]
                preview, pstart, pend = preview_around_match(
                    result.text, result.match_start, result.match_end, preview_limit
                )
                if self.color and pend > pstart:
                    preview = preview[:pstart] + "\x1b[7m" + preview[pstart:pend] + "\x1b[27m" + preview[pend:]
                prefix = "▶" if index == selected else " "
                label = f"[{result.pane_index + 1}] {result.pane_name}:{result.source_index + 1}"
                row = f"{prefix} {label}  {preview}"
                if index == selected:
                    row = core.paint(row, core.BOLD_LIGHT_CYAN, self.color)
                content.append(row)
            if self.global_search_truncated:
                content.append(core.paint(f"Showing first {GLOBAL_SEARCH_LIMIT} matches.", core.DIM, self.color))

        content.extend(["", "↑/↓ select · Enter jump · Tab mode · Esc close"])
        return _panel_lines("Global search", content, width, height, self.color)

    def _select_global_search_result(self) -> bool:
        self._refresh_global_search_results()
        if not self.global_search_results:
            return False
        result = self.global_search_results[self.global_search_selected]
        if result.pane_index >= len(self.panes):
            return False
        if self.layout == "stream":
            self.layout = "auto"
            self.maximized = False
        self.focus = result.pane_index
        pane = self.panes[result.pane_index]
        error = pane.set_search(
            self.global_search_buffer,
            self._search_flags(),
            mode=self.global_search_mode,
        )
        if error is not None:
            self.global_search_error = error
            return False
        inner_w, body_h = self._active_pane_geometry()
        pane.jump_to_source_line(result.source_index, inner_w, body_h)
        pane.set_message(
            f"global match {result.source_index + 1}: {search_label(self.global_search_buffer, self.global_search_mode)}",
            4.0,
        )
        self.global_search_active = False
        self.global_search_error = None
        self.dirty = True
        return True

    def _active_pane_geometry(self) -> Tuple[int, int]:
        target = -1 if self.layout == "stream" else self.focus
        rect = next((rect for index, rect in self.last_rects if index == target), None)
        if rect is None:
            width, height, _ = self.content_dimensions()
            return max(1, width - 2), max(1, height - 2)
        return max(1, rect.width - 2), max(1, rect.height - 2)

    def _help_lines(self, width: int, height: int) -> List[str]:
        content = [
            core.paint(f"htail {VERSION} — multi-file controls", core.BOLD, self.color),
            "",
            "Pane selection",
            "  Tab / Shift+Tab    next / previous pane",
            "  1–9                focus pane directly",
            "  mouse click        focus the pane under the pointer",
            "  mouse wheel        scroll the pane under the pointer",
            "",
            "Layout",
            "  l                  choose auto / rows / columns / grid / stream",
            "  z                  maximize focused pane / restore layout",
            "",
            "Focused pane",
            "  /                  search focused pane; Tab toggles Simple / Regex",
            "  n / N              next / previous local match",
            "  h                  set regex highlight; H clears it",
            "  ↑ ↓ / PgUp PgDn    scroll",
            "  [ / ]              previous / next update",
            "  f                  freshest update",
            "  p                  pause/resume automatic jumps",
            "  c                  clear displayed history; tracking continues",
            "",
            "Global",
            "  g                  live search across all watched files",
            "  u                  check/install updates",
            "  ?                  close help",
            "  q                  quit",
            "",
            "Mouse tracking can be disabled with --no-mouse.",
        ]
        return _panel_lines("Help", content, width, height, self.color)

    def _layout_menu_lines(self, width: int, height: int) -> List[str]:
        content = [
            "Choose layout without restarting:",
            "",
            core.paint("[A] Auto", core.BOLD_LIGHT_CYAN if self.layout == "auto" else core.BOLD, self.color),
            core.paint("[R] Rows", core.BOLD_LIGHT_CYAN if self.layout == "rows" else core.BOLD, self.color),
            core.paint("[C] Columns", core.BOLD_LIGHT_CYAN if self.layout == "columns" else core.BOLD, self.color),
            core.paint("[G] Grid", core.BOLD_LIGHT_CYAN if self.layout == "grid" else core.BOLD, self.color),
            core.paint("[S] Stream", core.BOLD_LIGHT_CYAN if self.layout == "stream" else core.BOLD, self.color),
            "",
            "L / Esc  cancel",
            "Pane scroll positions and pause state are preserved.",
        ]
        return _panel_lines("Layout", content, width, height, self.color)

    def _update_lines(self, width: int, height: int) -> List[str]:
        release = self.update_release
        if release is None:
            return _panel_lines("Update", ["No update is currently available.", "", "Press U to check GitHub again."], width, height, self.color)
        if self.update_installing:
            content: List[str] = [
                core.paint(f"htail {VERSION}  →  {release.version}", core.BOLD, self.color),
                core.paint(self.update_service.repo, core.DIM, self.color),
                "",
                self.update_install_status or "Preparing update…",
            ]
            frac = max(0.0, min(1.0, self.update_overall_progress))
            bar_w = max(12, min(40, width - 24))
            filled = int(round(bar_w * frac))
            bar = "[" + ("█" * filled) + ("░" * max(0, bar_w - filled)) + "]"
            content.append(core.paint(bar, core.BOLD_LIGHT_CYAN, self.color) + f"  {frac*100:5.1f}%")
            if self.update_install_progress is not None:
                current, total = self.update_install_progress
                if total and total > 0:
                    content.append(f"{current:,} / {total:,} bytes")
                elif current is not None:
                    content.append(f"{current:,} bytes downloaded")
            content.extend(["", "All watched files will reopen automatically when the update completes."])
            return _panel_lines("Installing update", content, width, height, self.color)
        features, fixes, other = core.release_note_sections(release.notes)
        content: List[str] = [
            core.paint(f"htail {VERSION}  →  {release.version}", core.BOLD, self.color),
            core.paint(self.update_service.repo, core.DIM, self.color),
            "",
        ]
        if features:
            content.append(core.paint("New features", core.BOLD_LIGHT_CYAN, self.color))
            content.extend(f"• {item}" for item in features[:5])
            content.append("")
        if fixes:
            content.append(core.paint("Bug fixes", core.BOLD_YELLOW, self.color))
            content.extend(f"• {item}" for item in fixes[:5])
            content.append("")
        if not features and not fixes:
            content.append(core.paint("Release notes", core.BOLD_LIGHT_CYAN, self.color))
            content.extend(f"• {item}" for item in other[:6])
            content.append("")
        content.extend([
            "A .bak copy of the current executable will be kept.",
            f"After updating, htail will reopen all {len(self.panes)} source{'s' if len(self.panes) != 1 else ''}.",
            "",
            core.paint("[Y] Update now", core.BOLD + core.GREEN, self.color) + "    " + core.paint("[N] Cancel", core.BOLD, self.color),
        ])
        return _panel_lines("Update available", content, width, height, self.color)

    def _install_worker(self, release: core.ReleaseInfo) -> None:
        """Install a confirmed release off the UI thread and schedule restart."""
        target = executable_path()

        def progress(stage: str, current: Optional[int], total: Optional[int]) -> None:
            self.update_install_status = stage
            self.update_install_progress = None if current is None else (current, total)
            if stage.startswith("Downloading"):
                if current is not None and total and total > 0:
                    self.update_overall_progress = 0.05 + 0.70 * max(0.0, min(1.0, current / total))
                else:
                    self.update_overall_progress = max(self.update_overall_progress, 0.10)
            elif stage.startswith("Verifying"):
                self.update_overall_progress = max(self.update_overall_progress, 0.80)
            elif stage.startswith("Backing up"):
                self.update_overall_progress = max(self.update_overall_progress, 0.90)
            elif stage.startswith("Installing") or stage.startswith("Replacing"):
                self.update_overall_progress = max(self.update_overall_progress, 0.97)
            else:
                self.update_overall_progress = max(self.update_overall_progress, 0.02)
            self.dirty = True

        try:
            progress("Preparing update…", None, None)
            ok, message = self.update_service.install(release, target, progress=progress)
        except Exception as exc:
            ok, message = False, f"update failed: {exc}"

        now = time.monotonic()
        self.update_install_result = (ok, message)
        if ok:
            self.update_install_status = "Update complete — restarting…"
            self.update_install_progress = None
            self.update_overall_progress = 1.0
            self.pending_restart = (target, list(sys.argv[1:]), message)
            started = self.update_progress_started_at if self.update_progress_started_at is not None else now
            self.pending_restart_at = max(started + MIN_UPDATE_MODAL_SECONDS, now + MIN_UPDATE_COMPLETE_SECONDS)
            # Keep the modal in its installing state until the delayed restart
            # so a very fast 70 KB download still visibly reaches 100%.
            self.update_installing = True
        else:
            self.update_installing = False
        self.dirty = True

    def _status_lines(self, width: int, body_height: int) -> List[str]:
        now = time.monotonic()
        lead = self.message if self.message and now <= self.message_until else None
        if lead is None:
            self.message = None
        layout_name = self.resolved_layout(width, body_height)
        parts = [f"htail {VERSION}", f"layout {self.layout}" + (f"→{layout_name}" if self.layout == "auto" else "")]
        if self.layout == "stream":
            parts.append("stream")
        elif self.panes:
            parts.append(f"focus {self.focus + 1}/{len(self.panes)} {self.panes[self.focus].name}")
            if self.maximized:
                parts.append("MAX")
        if lead:
            parts.append(lead)
        if self.update_installing:
            parts.append("UPDATING")
        elif self.update_release is not None:
            parts.append(f"UPDATE {self.update_release.version}")
        top = " · ".join(parts)
        controls = "/ search · g global · n/N match · h highlight · Tab pane · l layout · ↑↓/Pg scroll · [/] update · f newest · p pause · u update · q quit · ? help"
        return [top, controls]

    def _frame_rows(self) -> Tuple[int, List[str]]:
        width, body_height, footer_height = self.content_dimensions()
        base_body = self._pane_boxes(width, body_height)
        if self.global_search_active:
            body = _overlay_modal(base_body, self._global_search_lines(width, body_height), width, body_height, self.color)
        elif self.prompt_mode:
            body = _overlay_modal(base_body, self._prompt_lines(width, body_height), width, body_height, self.color)
        elif self.update_confirm_active:
            body = _overlay_modal(base_body, self._update_lines(width, body_height), width, body_height, self.color)
        elif self.layout_menu:
            body = _overlay_modal(base_body, self._layout_menu_lines(width, body_height), width, body_height, self.color)
        elif self.help_active:
            body = _overlay_modal(base_body, self._help_lines(width, body_height), width, body_height, self.color)
        else:
            body = base_body

        if self.global_search_active:
            status = [f"GLOBAL SEARCH · {self._search_mode_name(self.global_search_mode)} · ↑↓ select · Enter jump · Tab mode · Esc close", "Background watching continues while this dialog is open"]
        elif self.prompt_mode:
            if self.prompt_mode == "search":
                status = [f"SEARCH · {self._search_mode_name(self.prompt_search_mode)} · Tab mode · Enter apply · Esc cancel", "Background watching continues while this dialog is open"]
            else:
                status = ["REGEX HIGHLIGHT · Enter apply · Esc cancel", "Background watching continues while this dialog is open"]
        elif self.update_confirm_active:
            status = ["UPDATE · y confirm · n cancel", "Background watching continues while this dialog is open"]
        elif self.layout_menu:
            status = ["LAYOUT · a/r/c/g/s choose · l/Esc cancel", "Background watching continues while this dialog is open"]
        elif self.help_active:
            status = ["HELP · ? close · q quit", "Background watching continues while this dialog is open"]
        else:
            status = self._status_lines(width, body_height)

        frame = [_pad(body[row] if row < len(body) else "", width) for row in range(body_height)]
        for i in range(footer_height):
            line = status[i] if i < len(status) else ""
            line = _pad(core.clip_ansi(line, width), width)
            if self.color:
                line = core.REVERSE + line + core.RESET
            frame.append(line)
        return width, frame

    def render(self) -> None:
        if not self.dirty:
            return
        width, frame = self._frame_rows()
        geometry = (width, len(frame))
        full = self._last_frame is None or self._last_frame_geometry != geometry
        changed = list(range(len(frame))) if full else _changed_frame_rows(self._last_frame, frame)
        if full:
            sys.stdout.write(core.CLEAR_SCREEN)
        for row in changed:
            sys.stdout.write(f"\033[{row + 1};1H" + core.RESET + core.CLEAR_LINE)
            sys.stdout.write(frame[row] + core.RESET)
        if changed:
            sys.stdout.flush()
        self.render_rows_written += len(changed)
        self.render_frames += 1
        self._last_frame = list(frame)
        self._last_frame_geometry = geometry
        self.dirty = False

    def __enter__(self) -> "MultiApp":
        sys.stdout.write(core.ALT_SCREEN_ON + core.HIDE_CURSOR + core.CLEAR_SCREEN + core.CURSOR_HOME)
        sys.stdout.flush()
        self._last_frame = None
        self._last_frame_geometry = None
        self.dirty = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close_native_watch()
        for follower in self.followers:
            close = getattr(follower, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
        sys.stdout.write(core.SHOW_CURSOR + core.RESET + core.ALT_SCREEN_OFF)
        sys.stdout.flush()

    def _pane_at(self, x: int, y: int) -> Optional[int]:
        for index, rect in self.last_rects:
            if rect.contains(x, y):
                return index
        return None

    def handle_mouse(self, event: MouseEvent) -> None:
        # SGR mouse mode emits a release event after a click. Never let that
        # release move focus: its coordinates can be in a different pane if
        # the pointer moved after the press, which made focus appear to jump
        # back or require a second click.
        if event.button == "left" and not event.pressed:
            return

        target = self._pane_at(event.x, event.y)
        if target is None:
            return
        if target >= 0:
            self.focus = target
            pane = self.panes[target]
        else:
            pane = self.stream
        if event.button == "left":
            self.dirty = True
            return
        if event.button in ("wheel_up", "wheel_down"):
            rect = next((r for i, r in self.last_rects if i == target), None)
            body_h = max(1, (rect.height - 2) if rect else 5)
            for _ in range(3):
                pane.scroll("UP" if event.button == "wheel_up" else "DOWN", body_h)
            self.dirty = True

    def handle_input(self, event: InputEvent) -> bool:
        if self.global_search_active and not isinstance(event, MouseEvent):
            key = event
            if key == "ESC":
                self.global_search_active = False
                self.global_search_error = None
                self.dirty = True
                return False
            if key in ("TAB", "SHIFT_TAB"):
                self.global_search_mode = self._other_search_mode(self.global_search_mode)
                self.global_search_selected = 0
                self._refresh_global_search_results()
                self.dirty = True
                return False
            if key in ("UP", "DOWN", "PAGEUP", "PAGEDOWN"):
                self._refresh_global_search_results()
                if self.global_search_results:
                    delta = {"UP": -1, "DOWN": 1, "PAGEUP": -8, "PAGEDOWN": 8}[key]
                    self.global_search_selected = min(
                        max(0, self.global_search_selected + delta),
                        len(self.global_search_results) - 1,
                    )
                self.dirty = True
                return False
            if key in ("\r", "\n"):
                self._select_global_search_result()
                self.dirty = True
                return False
            if key in ("\x7f", "\b"):
                self.global_search_buffer = self.global_search_buffer[:-1]
                self.global_search_selected = 0
                self._refresh_global_search_results()
                self.dirty = True
                return False
            if isinstance(key, str) and len(key) == 1 and key.isprintable():
                self.global_search_buffer += key
                self.global_search_selected = 0
                self._refresh_global_search_results()
                self.dirty = True
            return False

        if self.prompt_mode and not isinstance(event, MouseEvent):
            key = event
            if key == "ESC":
                self.prompt_mode = None
                self.prompt_buffer = ""
                self.dirty = True
                return False
            if key in ("TAB", "SHIFT_TAB") and self.prompt_mode == "search":
                self.prompt_search_mode = self._other_search_mode(self.prompt_search_mode)
                self.dirty = True
                return False
            if key in ("\r", "\n"):
                pane = self.active_pane()
                flags = self._search_flags()
                if self.prompt_mode == "search":
                    error = pane.set_search(self.prompt_buffer, flags, mode=self.prompt_search_mode)
                    if error is None:
                        inner_w, body_h = self._active_pane_geometry()
                        pane.search_next(False, inner_w, body_h)
                else:
                    error = pane.set_highlight(self.prompt_buffer, flags)
                    if error is None:
                        pane.set_message(f"highlight /{self.prompt_buffer}/" if self.prompt_buffer else "regex highlight cleared")
                if error is not None:
                    self.set_message(f"invalid search: {error}", 5.0)
                self.prompt_mode = None
                self.prompt_buffer = ""
                self.dirty = True
                return False
            if key in ("\x7f", "\b"):
                self.prompt_buffer = self.prompt_buffer[:-1]
                self.dirty = True
                return False
            if isinstance(key, str) and len(key) == 1 and key.isprintable():
                self.prompt_buffer += key
                self.dirty = True
            return False

        if isinstance(event, MouseEvent):
            if not (self.help_active or self.layout_menu or self.update_confirm_active or self.global_search_active or self.prompt_mode):
                self.handle_mouse(event)
            return False

        key = event
        if self.update_confirm_active:
            if key in ("n", "N", "ESC", "q", "Q"):
                self.update_confirm_active = False
                self.set_message("update cancelled")
            elif key in ("y", "Y") and self.update_release is not None and not self.update_installing:
                if self.args.commands:
                    self.update_confirm_active = False
                    self.set_message("update not installed during --exec; run 'ht --update' separately", 6.0)
                    return False
                self.update_installing = True
                self.update_install_result = None
                self.update_install_status = "Preparing update…"
                self.update_install_progress = None
                self.update_overall_progress = 0.02
                self.update_progress_started_at = time.monotonic()
                self.pending_restart_at = None
                self.dirty = True
                threading.Thread(target=self._install_worker, args=(self.update_release,), daemon=True, name="htail-install").start()
            return False

        if key in ("q", "Q"):
            return True

        if self.layout_menu:
            mapping = {"a": "auto", "A": "auto", "r": "rows", "R": "rows", "c": "columns", "C": "columns", "g": "grid", "G": "grid", "s": "stream", "S": "stream"}
            if key in mapping:
                self.layout = mapping[key]
                self.layout_menu = False
                self.maximized = False
                self.set_message(f"layout: {self.layout}")
            elif key in ("l", "L", "ESC"):
                self.layout_menu = False
                self.dirty = True
            return False

        if self.help_active:
            if key == "?":
                self.help_active = False
                self.dirty = True
            return False

        if key == "?":
            self.help_active = True
            self.dirty = True
            return False
        if key in ("l", "L"):
            self.layout_menu = True
            self.dirty = True
            return False
        if key in ("z", "Z"):
            if self.layout == "stream":
                self.set_message("stream already uses the full viewport")
            else:
                self.maximized = not self.maximized
                self.dirty = True
            return False
        if key == "TAB":
            self.focus_next(1)
            return False
        if key == "SHIFT_TAB":
            self.focus_next(-1)
            return False
        if isinstance(key, str) and len(key) == 1 and key in "123456789":
            index = int(key) - 1
            if index < len(self.panes):
                self.focus = index
                self.dirty = True
            return False

        if key == "/":
            pane = self.active_pane()
            self.prompt_mode = "search"
            self.prompt_buffer = pane.search_pattern
            self.prompt_search_mode = pane.search_mode if pane.search_pattern else SEARCH_SIMPLE
            self.dirty = True
            return False
        if key in ("g", "G"):
            self.global_search_active = True
            self.global_search_selected = 0
            self._refresh_global_search_results()
            self.dirty = True
            return False
        if key == "h":
            self.prompt_mode = "highlight"
            self.prompt_buffer = self.active_pane().highlight_pattern
            self.dirty = True
            return False
        if key == "H":
            self.active_pane().clear_highlight()
            self.dirty = True
            return False
        if key in ("n", "N"):
            pane = self.active_pane()
            inner_w, body_h = self._active_pane_geometry()
            pane.search_next(key == "N", inner_w, body_h)
            self.dirty = True
            return False

        if key in ("u", "U"):
            if self.update_release is not None:
                self.update_confirm_active = True
                self.dirty = True
            elif self.update_service.enabled:
                self.update_manual_check_pending = True
                if self.update_service.refresh():
                    self.update_check_done = False
                    self.update_check_error = None
                    self.last_update_check_monotonic = time.monotonic()
                    self.set_message("checking GitHub for updates…")
                else:
                    self.set_message("update check already in progress")
            return False

        pane = self.active_pane()
        if key in ("p", "P"):
            pane.toggle_pause(); self.dirty = True; return False
        if key in ("c", "C"):
            pane.clear_display(); self.dirty = True; return False
        if key in ("f", "F"):
            pane.freshest(); self.dirty = True; return False
        if key == "[":
            pane.previous_update(); self.dirty = True; return False
        if key == "]":
            pane.next_update(); self.dirty = True; return False
        if key in ("UP", "DOWN", "PAGEUP", "PAGEDOWN", "HOME", "END"):
            rect = next((r for i, r in self.last_rects if (i == self.focus or (i == -1 and self.layout == "stream"))), None)
            body_h = max(1, (rect.height - 2) if rect else self.content_dimensions()[1] - 2)
            pane.scroll(key, body_h)
            self.dirty = True
            return False
        return False

    def _tick_updates(self, now: float) -> None:
        if not self.update_service.enabled:
            return
        if self.update_release is None and self.update_check_done and now - self.last_update_check_monotonic >= core.AUTO_UPDATE_CHECK_INTERVAL:
            if self.update_service.refresh():
                self.update_check_done = False
                self.update_check_error = None
                self.last_update_check_monotonic = now
        done, release, error = self.update_service.snapshot()
        changed = done != self.update_check_done or release != self.update_release or error != self.update_check_error
        self.update_check_done, self.update_release, self.update_check_error = done, release, error
        if changed and done:
            self.last_update_check_monotonic = now
            if release is not None:
                if self.update_manual_check_pending:
                    self.update_manual_check_pending = False
                    self.update_confirm_active = True
                    self.dirty = True
                else:
                    self.set_message(f"update {release.version} available — press u", 5.0)
            elif self.update_manual_check_pending:
                self.update_manual_check_pending = False
                self.set_message(f"update check failed: {error}" if error else "already on the latest release", 4.0)

    def tick(self, now: float) -> None:
        self._tick_updates(now)
        if self.update_install_result is not None and not self.update_installing:
            ok, message = self.update_install_result
            self.update_install_result = None
            if not ok:
                self.update_confirm_active = False
                self.set_message(message, 6.0)
        second = int(now)
        if second != self.last_status_second:
            self.last_status_second = second
            self.dirty = True

    def process_watchers(self, now: float) -> None:
        events = self.native_watch.poll()
        if self.native_watch.available:
            exact_paths = {Path(os.path.abspath(os.fspath(path))) for path in events.paths}
            dirty_dirs = {Path(os.path.abspath(os.fspath(path))) for path in events.directories}
            for follower in self.followers:
                if not isinstance(follower, FileFollower):
                    continue
                path = Path(os.path.abspath(os.fspath(follower.path)))
                if self.native_watch.backend == "inotify":
                    if path in exact_paths or path.parent in exact_paths:
                        follower.notify()
                elif path.parent in dirty_dirs:
                    follower.notify()
        else:
            # Poll fallback intentionally preserves the exact v0.9 scheduling.
            for follower in self.followers:
                if isinstance(follower, FileFollower):
                    follower.notify()

        self._refresh_globs(now, events)
        for index, follower in enumerate(self.followers):
            result = follower.poll(now)
            if result is None:
                continue
            pane = self.panes[index]
            if isinstance(result, WatchNotice):
                if result.kind in ("initial", "resumed") and result.initial_tail is not None:
                    pane.add_initial(result.initial_tail)
                    pane.set_snapshot(follower.previous)
                    self._stream_initial(index, pane, result.initial_tail)
                    if result.kind == "resumed":
                        pane.set_message("resumed")
                elif result.kind == "resumed":
                    pane.missing = False
                    pane.waiting = False
                    pane.set_message("resumed")
                elif result.kind == "missing":
                    pane.waiting = not follower.initialized
                    pane.missing = follower.initialized
                    pane.set_message("waiting for file", 4.0)
                elif result.kind == "ended":
                    pane.waiting = False
                    pane.missing = False
                    pane.set_message(result.text, 6.0)
                elif result.kind == "error":
                    pane.add_system_line(result.text, warning=True)
                self.dirty = True
                continue

            if isinstance(result, WatchUpdate):
                if pane.missing:
                    pane.add_system_line(f"resumed {pane.path}")
                header, rendered = pane.add_update(
                    result.update_number,
                    result.events,
                    result.added,
                    result.replaced,
                    result.deleted,
                    result.elapsed,
                    self.args.show_deletions,
                    self.args.mark_replacements,
                    result.now_monotonic,
                )
                pane.set_snapshot(
                    result.current_snapshot,
                    result.changed_new_indices,
                    prefer=not pane.paused,
                    update_header=header,
                )
                self.stream.add_source_update(index, pane.name, header, rendered, result.now_monotonic)
                self.dirty = True


class _CLIUpdateProgress:
    """Compact progress reporter for ``ht --update``."""

    def __init__(self, stream) -> None:
        self.stream = stream
        self.tty = bool(getattr(stream, "isatty", lambda: False)())
        self.last_stage: Optional[str] = None
        self.open_line = False

    def _newline(self) -> None:
        if self.open_line:
            self.stream.write("\n")
            self.open_line = False

    def __call__(self, stage: str, current: Optional[int], total: Optional[int]) -> None:
        if not self.tty:
            if stage != self.last_stage:
                self.stream.write(f"[htail] {stage}\n")
                self.stream.flush()
            self.last_stage = stage
            return
        if stage != self.last_stage:
            self._newline()
        self.last_stage = stage
        if current is None:
            text = f"[htail] {stage}"
        elif total and total > 0:
            frac = max(0.0, min(1.0, current / total))
            filled = int(round(30 * frac))
            bar = "█" * filled + "░" * (30 - filled)
            text = f"[htail] [{bar}] {frac * 100:5.1f}%  {current:,}/{total:,} bytes"
        else:
            text = f"[htail] Downloading… {current:,} bytes"
        self.stream.write("\r" + core.CLEAR_LINE + text)
        self.stream.flush()
        self.open_line = True

    def finish(self) -> None:
        self._newline()
        self.stream.flush()


def run_interactive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter, update_service: core.UpdateService) -> int:
    # Interactive panes always retain the full current file; geometry decides the viewport.
    args.lines = None
    app = MultiApp(args, color, display_filter, update_service)
    update_service.start()
    restart: Optional[core.RestartRequested] = None
    next_watch_poll = 0.0
    next_pid_check = 0.0
    try:
        with app, InputReader(mouse=not args.no_mouse) as reader:
            while True:
                frame_started = time.monotonic()
                quit_requested = False
                for _ in range(128):
                    event = reader.poll()
                    if event is None:
                        break
                    if app.handle_input(event):
                        quit_requested = True
                        break
                if quit_requested:
                    break

                now = time.monotonic()
                if args.pid is not None and now >= next_pid_check:
                    next_pid_check = now + 0.25
                    if not _process_alive(args.pid):
                        app.set_message(f"pid {args.pid} exited", 1.0)
                        app.render()
                        break
                if now >= next_watch_poll:
                    app.process_watchers(now)
                    next_watch_poll = now + args.interval
                app.tick(now)
                if app.pending_restart is not None and (
                    app.pending_restart_at is None or now >= app.pending_restart_at
                ):
                    target, argv, message = app.pending_restart
                    raise core.RestartRequested(target, argv, message)
                app.render()
                elapsed = time.monotonic() - frame_started
                time.sleep(max(0.0, INTERACTIVE_FRAME_INTERVAL - elapsed))
    except core.RestartRequested as exc:
        restart = exc
    except KeyboardInterrupt:
        pass

    if restart is not None:
        print(f"[htail] {restart.message}; reopening watched files", flush=True)
        os.execv(sys.executable, [sys.executable, str(restart.target), *restart.argv])
    return 0


def _render_stream_event(index: int, pane: Pane, result: WatchUpdate, args: argparse.Namespace, display_filter: core.DisplayFilter, color: bool) -> None:
    filtered, visible_count = display_filter.apply_events(result.events)
    if not args.show_deletions:
        visible_count -= sum(len(lines) for kind, lines in filtered if kind == "delete")
        visible_count = max(0, visible_count)
    total = result.added + result.replaced + (result.deleted if args.show_deletions else 0)
    header = core.format_update_header(result.update_number, result.added, result.replaced, result.deleted if args.show_deletions else 0, result.elapsed, visible_count, total, display_filter.active, color)
    rendered = core.render_event_lines(filtered, pane.highlighter, color, args.show_deletions, args.mark_replacements)
    print(f"\n{core.paint(f'━━ [{index + 1}] {pane.name} ━━', core.BOLD_LIGHT_CYAN, color)}")
    print(header)
    for line in rendered:
        print(line)


def run_noninteractive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter) -> int:
    if args.lines is None:
        args.lines = 50

    glob_trackers = [
        DynamicGlob(str(path))
        for path in args.files
        if str(path) != "-" and has_magic(str(path))
    ]
    glob_trackers.extend(DynamicGlob(pattern) for pattern in args.globs)
    initial_paths = [
        path for path in args.files
        if str(path) == "-" or not has_magic(str(path))
    ]
    for tracker in glob_trackers:
        initial_paths.extend(tracker.scan())

    panes: List[Pane] = []
    followers: List[object] = []
    known_paths = set()

    def add_file_source(path: Path) -> bool:
        if str(path) == "-":
            key = ("stdin",)
        else:
            key = ("file", os.path.abspath(os.fspath(path)))
        if key in known_paths:
            return False
        known_paths.add(key)

        if str(path) == "-":
            pseudo = Path("stdin.txt")
            highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)
            pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name="stdin")
            follower = StreamFollower(sys.stdin, args, label="stdin")
        else:
            highlighter = core.SyntaxHighlighter(path, args.syntax, color)
            pane = Pane(path, highlighter, display_filter, color, args.idle_warn)
            follower = FileFollower(path, args)
        notice = follower.initialize_if_available()
        panes.append(pane)
        followers.append(follower)
        if notice and notice.initial_tail is not None:
            if not args.no_start_banner:
                print(f"[htail {VERSION}] [{len(panes)}] watching {pane.name} · syntax: {highlighter.syntax_name}")
            visible = [line for line in notice.initial_tail if display_filter.accepts(line)]
            for line in core.render_initial_lines(visible, highlighter):
                print(line)
        elif not args.no_start_banner:
            print(f"[htail] [{len(panes)}] waiting for {pane.name}", file=sys.stderr)
        return True

    for path in initial_paths:
        add_file_source(path)

    for command_index, command in enumerate(args.commands, start=1):
        pseudo = Path(f"command-{command_index}.log")
        highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)
        pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name=f"$ {command}")
        follower = CommandFollower(command, args, label=pane.name)
        follower.initialize_if_available()
        panes.append(pane)
        followers.append(follower)
        if not args.no_start_banner:
            print(f"[htail {VERSION}] [{len(panes)}] running {command} (pid {follower.process.pid})")

    next_glob_scan = 0.0
    try:
        while True:
            time.sleep(args.interval)
            now = time.monotonic()
            if args.pid is not None and not _process_alive(args.pid):
                return 0

            if glob_trackers and now >= next_glob_scan:
                next_glob_scan = now + 2.0
                for tracker in glob_trackers:
                    for path in tracker.scan():
                        add_file_source(path)

            for index, follower in list(enumerate(followers)):
                result = follower.poll(now)
                if isinstance(result, WatchUpdate):
                    _render_stream_event(index, panes[index], result, args, display_filter, color)
                elif isinstance(result, WatchNotice) and result.kind == "error":
                    print(f"[htail] [{index + 1}] {result.text}", file=sys.stderr)
            if followers and not glob_trackers and all(bool(getattr(follower, "finished", False)) for follower in followers):
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        for follower in followers:
            close = getattr(follower, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    core.enable_windows_ansi()
    color = sys.stdout.isatty() and not args.no_color
    maybe_offer_self_install(args, color)

    update_repo = os.environ.get("HTAIL_UPDATE_REPO", core.DEFAULT_UPDATE_REPO).strip()
    update_service = core.UpdateService(update_repo)
    if args.check_update or args.update:
        try:
            release = update_service.check_latest()
        except Exception as exc:
            print(f"htail: {exc}", file=sys.stderr)
            return 1
        if release is None:
            print(f"htail {VERSION} is the latest published release.")
            return 0
        if args.check_update:
            print(f"htail {release.version} is available (current: {VERSION}).")
            return 0
        cli_progress = _CLIUpdateProgress(sys.stdout)
        ok, message = update_service.install(release, executable_path(), progress=cli_progress)
        cli_progress.finish()
        print(f"[htail] {message}", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if not args.files and not args.commands and not args.globs and not sys.stdin.isatty():
        args.files = [Path("-")]

    if not args.files and not args.commands and not args.globs:
        print(f"htail {VERSION}")
        print("Usage: ht FILE [FILE ...] | ht --glob 'logs/*.log' | producer | ht | ht --exec COMMAND")
        print("Example: ht reviewer.md implementer.md")
        return 0

    try:
        display_filter = core.compile_display_filter(args)
    except ValueError as exc:
        print(f"htail: {exc}", file=sys.stderr)
        return 2

    core.maybe_offer_pygments_install(args, color)
    has_stdin_source = any(str(path) == "-" for path in args.files)
    interactive = sys.stdout.isatty() and (sys.stdin.isatty() or has_stdin_source or bool(args.commands) or bool(args.globs))
    if interactive:
        return run_interactive(args, color, display_filter, update_service)
    return run_noninteractive(args, color, display_filter)
