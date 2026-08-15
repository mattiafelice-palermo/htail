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
from .layout import LAYOUTS, Rect, pane_rects, resolve_auto
from .pane import Pane, StreamPane
from .watcher import FileFollower, WatchNotice, WatchUpdate

# The reusable core is copied from the previous single-file implementation.
# Override its runtime version so UpdateService and all status text use the
# packaged application version rather than the legacy source snapshot.
core.HTAIL_VERSION = VERSION


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
    parser.add_argument("files", type=Path, nargs="*", help="text files to watch")
    parser.add_argument("--install", nargs="?", const=core.DEFAULT_INSTALL_COMMAND, metavar="NAME")
    parser.add_argument("--no-self-install-prompt", action="store_true")
    parser.add_argument("--check-update", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("-n", "--lines", type=int, default=50, help="initial lines per file (default: 50)")
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
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.lines < 0:
        parser.error("--lines must be >= 0")
    if args.interval <= 0:
        parser.error("--interval must be > 0")
    if args.verify_interval < 0:
        parser.error("--verify-interval must be >= 0")
    if args.debounce < 0 or args.max_debounce < 0:
        parser.error("--debounce and --max-debounce must be >= 0")
    if args.idle_warn < 0:
        parser.error("--idle-warn must be >= 0")
    return args


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
    if not color or not text.strip():
        return text
    return core.DIM + text + core.RESET


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
        self.paths = list(args.files)
        self.followers: List[FileFollower] = []
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
        self.update_install_result: Optional[Tuple[bool, str]] = None
        self.update_release: Optional[core.ReleaseInfo] = None
        self.pending_restart: Optional[Tuple[Path, List[str], str]] = None
        self.update_check_done = False
        self.update_check_error: Optional[str] = None
        self.update_manual_check_pending = False
        self.last_update_check_monotonic = time.monotonic()
        self.message: Optional[str] = None
        self.message_until = 0.0
        self.last_status_second: Optional[int] = None
        self.last_rects: List[Tuple[int, Rect]] = []
        self.dirty = True

        for path in self.paths:
            highlighter = core.SyntaxHighlighter(path, args.syntax, color)
            pane = Pane(path, highlighter, display_filter, color, args.idle_warn)
            follower = FileFollower(path, args)
            notice = follower.initialize_if_available()
            if notice and notice.initial_tail is not None:
                pane.add_initial(notice.initial_tail)
                self._stream_initial(len(self.panes), pane, notice.initial_tail)
            else:
                pane.waiting = True
                if notice and notice.kind == "error":
                    pane.add_system_line(notice.text, warning=True)
            if highlighter.warning:
                pane.add_system_line(highlighter.warning, warning=True)
            self.panes.append(pane)
            self.followers.append(follower)

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
            "  ↑ ↓ / PgUp PgDn    scroll",
            "  [ / ]              previous / next update",
            "  f                  freshest update",
            "  p                  pause/resume automatic jumps",
            "  c                  clear displayed history; tracking continues",
            "",
            "Global",
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
            if self.update_install_progress is not None:
                current, total = self.update_install_progress
                if total and total > 0:
                    frac = max(0.0, min(1.0, current / total))
                    bar_w = max(12, min(40, width - 24))
                    filled = int(round(bar_w * frac))
                    bar = "[" + ("█" * filled) + ("░" * max(0, bar_w - filled)) + "]"
                    content.append(core.paint(bar, core.BOLD_LIGHT_CYAN, self.color) + f"  {frac*100:5.1f}%")
                    content.append(f"{current:,} / {total:,} bytes")
                else:
                    step = (int(time.monotonic() * 8) % 12)
                    spinner = ''.join('█' if i == step else '░' for i in range(12))
                    content.append(core.paint(f"[{spinner}]", core.BOLD_LIGHT_CYAN, self.color) + "  downloading…")
                    content.append(f"{current:,} bytes")
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
            f"After updating, htail will reopen all {len(self.paths)} watched file{'s' if len(self.paths) != 1 else ''}.",
            "",
            core.paint("[Y] Update now", core.BOLD + core.GREEN, self.color) + "    " + core.paint("[N] Cancel", core.BOLD, self.color),
        ])
        return _panel_lines("Update available", content, width, height, self.color)

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
        controls = "Tab pane · click focus · l layout · z max · ↑↓/Pg scroll · [/] update · f newest · p pause · u check · q quit · ? help"
        return [top, controls]

    def render(self) -> None:
        if not self.dirty:
            return
        width, body_height, footer_height = self.content_dimensions()
        base_body = self._pane_boxes(width, body_height)
        if self.update_confirm_active:
            body = _overlay_modal(base_body, self._update_lines(width, body_height), width, body_height, self.color)
        elif self.layout_menu:
            body = _overlay_modal(base_body, self._layout_menu_lines(width, body_height), width, body_height, self.color)
        elif self.help_active:
            body = _overlay_modal(base_body, self._help_lines(width, body_height), width, body_height, self.color)
        else:
            body = base_body

        sys.stdout.write(core.CURSOR_HOME)
        for row in range(body_height):
            sys.stdout.write(core.CLEAR_LINE)
            sys.stdout.write(_pad(body[row] if row < len(body) else "", width))
            if row < body_height - 1:
                sys.stdout.write("\n")

        if self.update_confirm_active:
            status = ["UPDATE · y confirm · n cancel", "Background watching continues while this dialog is open"]
        elif self.layout_menu:
            status = ["LAYOUT · a/r/c/g/s choose · l/Esc cancel", "Background watching continues while this dialog is open"]
        elif self.help_active:
            status = ["HELP · ? close · q quit", "Background watching continues while this dialog is open"]
        else:
            status = self._status_lines(width, body_height)

        for i in range(footer_height):
            sys.stdout.write("\n" + core.CLEAR_LINE)
            line = status[i] if i < len(status) else ""
            if self.color:
                sys.stdout.write(core.REVERSE + core.clip_ansi(line, width) + core.RESET)
            else:
                sys.stdout.write(core.clip_ansi(line, width))
        sys.stdout.flush()
        self.dirty = False

    def __enter__(self) -> "MultiApp":
        sys.stdout.write(core.ALT_SCREEN_ON + core.HIDE_CURSOR + core.CLEAR_SCREEN + core.CURSOR_HOME)
        sys.stdout.flush()
        self.dirty = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        sys.stdout.write(core.SHOW_CURSOR + core.RESET + core.ALT_SCREEN_OFF)
        sys.stdout.flush()

    def _pane_at(self, x: int, y: int) -> Optional[int]:
        for index, rect in self.last_rects:
            if rect.contains(x, y):
                return index
        return None

    def handle_mouse(self, event: MouseEvent) -> None:
        target = self._pane_at(event.x, event.y)
        if target is None:
            return
        if target >= 0:
            self.focus = target
            pane = self.panes[target]
        else:
            pane = self.stream
        if event.button == "left" and event.pressed:
            self.dirty = True
            return
        if event.button in ("wheel_up", "wheel_down"):
            rect = next((r for i, r in self.last_rects if i == target), None)
            body_h = max(1, (rect.height - 2) if rect else 5)
            for _ in range(3):
                pane.scroll("UP" if event.button == "wheel_up" else "DOWN", body_h)
            self.dirty = True

    def handle_input(self, event: InputEvent) -> bool:
        if isinstance(event, MouseEvent):
            if not (self.help_active or self.layout_menu or self.update_confirm_active):
                self.handle_mouse(event)
            return False

        key = event
        if self.update_confirm_active:
            if key in ("n", "N", "ESC", "q", "Q"):
                self.update_confirm_active = False
                self.set_message("update cancelled")
            elif key in ("y", "Y") and self.update_release is not None and not self.update_installing:
                self.update_installing = True
                self.update_install_result = None
                self.update_install_status = "Preparing update…"
                self.update_install_progress = None
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
                self.update_manual_check_pending = False
                self.set_message(f"update {release.version} available — press u", 5.0)
            elif self.update_manual_check_pending:
                self.update_manual_check_pending = False
                self.set_message(f"update check failed: {error}" if error else "already on the latest release", 4.0)

    def tick(self, now: float) -> None:
        self._tick_updates(now)
        if self.update_install_result is not None and not self.update_installing:
            ok, message = self.update_install_result
            self.update_install_result = None
            if ok:
                # restart handled by run_interactive after this tick
                self.set_message(message, 4.0)
            else:
                self.update_confirm_active = False
                self.set_message(message, 6.0)
        second = int(now)
        if second != self.last_status_second:
            self.last_status_second = second
            self.dirty = True

    def process_watchers(self, now: float) -> None:
        for index, follower in enumerate(self.followers):
            result = follower.poll(now)
            if result is None:
                continue
            pane = self.panes[index]
            if isinstance(result, WatchNotice):
                if result.kind in ("initial", "resumed") and result.initial_tail is not None:
                    pane.add_initial(result.initial_tail)
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
                self.stream.add_source_update(index, pane.name, header, rendered, result.now_monotonic)
                self.dirty = True


def run_interactive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter, update_service: core.UpdateService) -> int:
    app = MultiApp(args, color, display_filter, update_service)
    update_service.start()
    restart: Optional[core.RestartRequested] = None
    try:
        with app, InputReader(mouse=not args.no_mouse) as reader:
            while True:
                event = reader.poll()
                if event is not None and app.handle_input(event):
                    break
                now = time.monotonic()
                app.process_watchers(now)
                app.tick(now)
                if app.pending_restart is not None:
                    target, argv, message = app.pending_restart
                    raise core.RestartRequested(target, argv, message)
                app.render()
                time.sleep(args.interval)
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
    panes: List[Pane] = []
    followers: List[FileFollower] = []
    for index, path in enumerate(args.files):
        highlighter = core.SyntaxHighlighter(path, args.syntax, color)
        pane = Pane(path, highlighter, display_filter, color, args.idle_warn)
        follower = FileFollower(path, args)
        notice = follower.initialize_if_available()
        panes.append(pane); followers.append(follower)
        if notice and notice.initial_tail is not None:
            if not args.no_start_banner:
                print(f"[htail {VERSION}] [{index + 1}] watching {path} · syntax: {highlighter.syntax_name}")
            visible = [line for line in notice.initial_tail if display_filter.accepts(line)]
            for line in core.render_initial_lines(visible, highlighter):
                print(line)
        elif not args.no_start_banner:
            print(f"[htail] [{index + 1}] waiting for {path}", file=sys.stderr)
    try:
        while True:
            time.sleep(args.interval)
            now = time.monotonic()
            for index, follower in enumerate(followers):
                result = follower.poll(now)
                if isinstance(result, WatchUpdate):
                    _render_stream_event(index, panes[index], result, args, display_filter, color)
                elif isinstance(result, WatchNotice) and result.kind == "error":
                    print(f"[htail] [{index + 1}] {result.text}", file=sys.stderr)
    except KeyboardInterrupt:
        return 0


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
        ok, message = update_service.install(release, executable_path())
        print(f"[htail] {message}", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if not args.files:
        print(f"htail {VERSION}")
        print("Usage: ht FILE [FILE ...]")
        print("Example: ht reviewer.md implementer.md")
        return 0

    try:
        display_filter = core.compile_display_filter(args)
    except ValueError as exc:
        print(f"htail: {exc}", file=sys.stderr)
        return 2

    core.maybe_offer_pygments_install(args, color)
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive:
        return run_interactive(args, color, display_filter, update_service)
    return run_noninteractive(args, color, display_filter)
