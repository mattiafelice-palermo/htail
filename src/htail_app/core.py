#!/usr/bin/env python3
"""
htail - a smarter interactive "tail -f" for human-readable files.

Core behavior
-------------
* -n/--lines limits only the initial context. Every later observed change is
  retained; updates are never clipped to the initial line count.
* Each update gets a numbered, timestamped header.
* In an interactive terminal, each new update opens at its HEADER (not its
  end). If another update arrives, htail opens the newer update at its header.
* Press p to pause viewport jumps while continuing to capture changes. Resume
  jumps to the newest captured update.
* Markdown files are rendered directly in the terminal (headings, emphasis,
  lists, links, rules, and fenced code). Pygments provides syntax highlighting
  for code files and fenced code blocks; if missing, interactive runs offer to
  install it.
* Added/replacement lines remain visually distinct without destroying syntax
  colours: syntax-highlighted files use a bold light-cyan gutter.
* Rewrites, truncations, and atomic file replacements are handled by diffing
  the previous and current snapshots.
* Display filters never affect the internal snapshot/diff state.

Interactive keys
----------------
  q          quit
  ?          toggle in-app help
  p          pause/resume automatic jumps to new updates
  c          clear displayed history (watch state is preserved)
  u          check for updates / confirm an available update
  f          jump to the freshest update header
  [ / ]      previous / next captured update
  Up/Down    scroll one line
  PgUp/PgDn  scroll one page
  Home/End   first line / bottom of displayed history

Dependencies
------------
No required third-party packages. Pygments is optional and can be installed
from the startup prompt. On first interactive launch, htail can install itself
as the short `ht` command in ~/.local/bin.

Limitation
----------
Like any watcher, htail cannot reconstruct an intermediate file version that
was created and completely overwritten between two observations. The polling
interval and debounce can be tuned for unusually fast writers.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Pattern, Sequence, Tuple

from .text_safety import sanitize_source_line


# ---------------------------------------------------------------------------
# ANSI / colour helpers
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
UNDERLINE = "\033[4m"
ITALIC = "\033[3m"
BOLD_LIGHT_CYAN = "\033[1;96m"
BOLD_YELLOW = "\033[1;93m"
BOLD_RED = "\033[1;91m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
REVERSE = "\033[7m"
CLEAR_SCREEN = "\033[2J"
CURSOR_HOME = "\033[H"
CLEAR_LINE = "\033[2K"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
ALT_SCREEN_ON = "\033[?1049h"
ALT_SCREEN_OFF = "\033[?1049l"

ANSI_RE = re.compile(r"(?:\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~])")

HTAIL_VERSION = "0.7.3"
ACTIVE_VERIFY_WINDOW = 15.0
AUTO_UPDATE_CHECK_INTERVAL = 3600.0
# Public GitHub repository used for background release checks and self-update.
# HTAIL_UPDATE_REPO can override this for development/testing.
DEFAULT_UPDATE_REPO = "mattiafelice-palermo/htail"
DEFAULT_UPDATE_ASSET = "htail"
DEFAULT_INSTALL_COMMAND = "ht"
APP_CONFIG_DIR = Path.home() / ".config" / "htail"
APP_STATE_FILE = APP_CONFIG_DIR / "state.json"


def enable_windows_ansi() -> None:
    """Enable ANSI escape processing in classic Windows consoles when possible."""
    if os.name != "nt" or not sys.stdout.isatty():
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            kernel32.SetConsoleMode(
                handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
    except Exception:
        pass


def paint(text: str, style: str, enabled: bool) -> str:
    return f"{style}{text}{RESET}" if enabled else text


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def clip_ansi(text: str, width: int) -> str:
    """Clip ANSI-styled text to approximately `width` printable characters."""
    if width <= 0:
        return ""

    visible = 0
    out: List[str] = []
    pos = 0

    for match in ANSI_RE.finditer(text):
        plain = text[pos : match.start()]
        if plain:
            room = width - visible
            if room <= 0:
                break
            chunk = plain[:room]
            out.append(chunk)
            visible += len(chunk)
            if len(chunk) < len(plain):
                break
        out.append(match.group(0))
        pos = match.end()

    if visible < width and pos < len(text):
        room = width - visible
        out.append(text[pos : pos + room])

    # Ensure styles and OSC-8 hyperlink state cannot leak into the following row.
    if "\x1b]8;;" in text:
        out.append("\x1b]8;;\x1b\\")
    out.append(RESET)
    return "".join(out)


def visible_prefix_ansi(text: str, visible_chars: int) -> str:
    """Return the first `visible_chars` printable characters, preserving ANSI."""
    if visible_chars <= 0:
        return ""

    visible = 0
    out: List[str] = []
    i = 0
    while i < len(text) and visible < visible_chars:
        match = ANSI_RE.match(text, i)
        if match:
            out.append(match.group(0))
            i = match.end()
            continue
        out.append(text[i])
        visible += 1
        i += 1

    return "".join(out)


def wrap_ansi(text: str, width: int) -> List[str]:
    """Soft-wrap ANSI-styled text to `width` printable columns.

    Markdown-style list items use a hanging indent: continuation rows align
    with the first character of the list item's text rather than with its
    bullet/number marker. Changed rows that start with the highlighted gutter
    keep that gutter on wrapped continuation rows.
    """
    if width <= 0:
        return [""]

    plain_text = strip_ansi(text)
    if len(plain_text) <= width:
        return [text + RESET]

    change_marker_visible = ""
    change_marker_ansi = ""
    body_text = plain_text
    if plain_text.startswith("▌ ") or plain_text.startswith("~ "):
        change_marker_visible = plain_text[:2]
        change_marker_ansi = visible_prefix_ansi(text, 2)
        # visible_prefix_ansi deliberately stops after the requested printable
        # characters, which means a painted gutter's trailing RESET is not
        # included. Re-close the gutter style before reusing it on continuation
        # rows; otherwise cyan/bold leaks into wrapped Markdown text.
        if ANSI_RE.search(change_marker_ansi) and not change_marker_ansi.endswith(RESET):
            change_marker_ansi += RESET
        body_text = plain_text[2:]

    # The Markdown renderer has already converted source markers to terminal
    # markers by this stage, so infer the visible content column from those
    # rendered forms. This also preserves nesting indentation.
    body_hanging_indent = 0
    list_match = re.match(r"^(?:\s*)(?:[•☐☑]|\d+[.)])\s+", body_text)
    if list_match:
        body_hanging_indent = list_match.end()

    continuation_prefix = change_marker_ansi + (" " * body_hanging_indent)
    continuation_visible_width = len(change_marker_visible) + body_hanging_indent

    lines: List[str] = []
    active = ""
    current: List[str] = [active]
    visible = 0
    last_space_index: Optional[int] = None
    last_space_visible = 0
    i = 0

    while i < len(text):
        match = ANSI_RE.match(text, i)
        if match:
            code = match.group(0)
            current.append(code)
            if code.endswith('m'):
                if code == RESET:
                    active = ""
                else:
                    active += code
            i = match.end()
            continue

        ch = text[i]
        current.append(ch)
        visible += 1
        if ch in (' ', '	'):
            last_space_index = len(current)
            last_space_visible = visible
        i += 1

        if visible >= width:
            if last_space_index is not None and last_space_visible >= max(1, width // 2):
                segment = "".join(current[:last_space_index]).rstrip()
                remainder_plain = "".join(current[last_space_index:]).lstrip(' 	')
                lines.append(segment + RESET)
                current = [continuation_prefix, active] if continuation_visible_width else [active]
                if remainder_plain:
                    current.append(remainder_plain)
                    visible = continuation_visible_width + len(strip_ansi(remainder_plain))
                else:
                    visible = continuation_visible_width
                last_space_index = None
                last_space_visible = 0
            else:
                lines.append("".join(current) + RESET)
                current = [continuation_prefix, active] if continuation_visible_width else [active]
                visible = continuation_visible_width
                last_space_index = None
                last_space_visible = 0

    tail = "".join(current)
    if tail == active:
        tail = ""
    lines.append(tail + RESET)
    return lines


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minute = divmod(minutes, 60)
    return f"{hours}h {minute:02d}m"


# ---------------------------------------------------------------------------
# Optional Pygments loading / installation
# ---------------------------------------------------------------------------

HAVE_PYGMENTS = False
pygments_highlight = None
Terminal256Formatter = None
get_lexer_by_name = None
get_lexer_for_filename = None
ClassNotFound = Exception


def load_pygments() -> bool:
    """Load/reload Pygments, including after an in-process pip installation."""
    global HAVE_PYGMENTS
    global pygments_highlight, Terminal256Formatter
    global get_lexer_by_name, get_lexer_for_filename, ClassNotFound

    importlib.invalidate_caches()
    try:
        from pygments import highlight
        from pygments.formatters import Terminal256Formatter as Formatter
        from pygments.lexers import get_lexer_by_name as by_name
        from pygments.lexers import get_lexer_for_filename as for_filename
        from pygments.util import ClassNotFound as PygmentsClassNotFound
    except ImportError:
        HAVE_PYGMENTS = False
        return False

    pygments_highlight = highlight
    Terminal256Formatter = Formatter
    get_lexer_by_name = by_name
    get_lexer_for_filename = for_filename
    ClassNotFound = PygmentsClassNotFound
    HAVE_PYGMENTS = True
    return True


load_pygments()


def maybe_offer_pygments_install(args: argparse.Namespace, color: bool) -> None:
    """Offer a one-time interactive Pygments installation when useful."""
    if HAVE_PYGMENTS:
        return
    if args.syntax.lower() == "none" or args.no_color or args.no_install_prompt:
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return

    print(
        paint("[htail] Pygments is not installed.", BOLD_YELLOW, color)
        + " Install it now for richer syntax highlighting? [Y/n] ",
        end="",
        flush=True,
    )
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if answer not in ("", "y", "yes"):
        return

    print("[htail] installing Pygments with this Python interpreter...", flush=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "Pygments"],
            check=False,
        )
    except OSError as exc:
        print(
            paint(f"[htail] could not start pip: {exc}", BOLD_YELLOW, color),
            file=sys.stderr,
        )
        return

    if result.returncode != 0 or not load_pygments():
        print(
            paint(
                "[htail] Pygments installation did not succeed; using available fallback highlighting.",
                BOLD_YELLOW,
                color,
            ),
            file=sys.stderr,
        )
    else:
        print("[htail] Pygments installed successfully.", flush=True)


# ---------------------------------------------------------------------------
# Keyboard input
# ---------------------------------------------------------------------------


class KeyReader:
    """Cross-platform, non-blocking single-key input for interactive terminals."""

    def __init__(self) -> None:
        self.enabled = False
        self._old_termios = None
        self._fd: Optional[int] = None

    def __enter__(self) -> "KeyReader":
        if not sys.stdin.isatty():
            return self

        self.enabled = True

        if os.name != "nt":
            try:
                import termios
                import tty

                self._fd = sys.stdin.fileno()
                self._old_termios = termios.tcgetattr(self._fd)
                tty.setcbreak(self._fd)
            except Exception:
                self.enabled = False
                self._fd = None
                self._old_termios = None

        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if os.name != "nt" and self._old_termios is not None and self._fd is not None:
            try:
                import termios

                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_termios)
            except Exception:
                pass

    def poll(self) -> Optional[str]:
        if not self.enabled:
            return None

        if os.name == "nt":
            try:
                import msvcrt

                if not msvcrt.kbhit():
                    return None
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0") and msvcrt.kbhit():
                    special = msvcrt.getwch()
                    return {
                        "H": "UP",
                        "P": "DOWN",
                        "I": "PAGEUP",
                        "Q": "PAGEDOWN",
                        "G": "HOME",
                        "O": "END",
                    }.get(special)
                return ch
            except Exception:
                return None

        try:
            import select

            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if not ready:
                return None

            ch = sys.stdin.read(1)
            if ch != "\x1b":
                return ch

            # ANSI navigation-key escape sequences. Read only bytes already
            # queued so a partial sequence never blocks the file watcher.
            seq = ch
            deadline = time.monotonic() + 0.025
            while time.monotonic() < deadline and len(seq) < 8:
                more, _, _ = select.select([sys.stdin], [], [], 0.002)
                if not more:
                    break
                seq += sys.stdin.read(1)

            return {
                "\x1b[A": "UP",
                "\x1b[B": "DOWN",
                "\x1b[5~": "PAGEUP",
                "\x1b[6~": "PAGEDOWN",
                "\x1b[H": "HOME",
                "\x1b[F": "END",
                "\x1bOH": "HOME",
                "\x1bOF": "END",
            }.get(seq)
        except Exception:
            return None


def sleep_responsive(duration: float, keys: KeyReader, key_handler=None) -> bool:
    """Sleep while processing keys. Return True when quit is requested."""
    deadline = time.monotonic() + max(0.0, duration)

    while True:
        ch = keys.poll()
        if ch in ("q", "Q"):
            return True
        if ch is not None and key_handler is not None:
            if key_handler(ch):
                return True

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.04, remaining))


# ---------------------------------------------------------------------------
# File snapshots / diffs
# ---------------------------------------------------------------------------


def read_lines(path: Path, encoding: str) -> List[str]:
    with path.open("r", encoding=encoding, errors="replace", newline="") as f:
        return f.readlines()


def read_verified_snapshot(
    path: Path,
    encoding: str,
    *,
    retries: int = 4,
    retry_delay: float = 0.03,
) -> Tuple[List[str], Optional[Tuple[int, int, int]]]:
    """Read a snapshot that was not visibly changing during the read.

    A writer can truncate/rewrite a file while htail is reading it.  Reading
    between two matching metadata signatures avoids committing an obviously
    torn snapshot.  The caller still performs normal debounce/coalescing; this
    is an additional integrity check around the actual read.

    The final attempt is returned even if the file remains busy so htail never
    blocks indefinitely on a continuously-written log.
    """
    last_lines: List[str] = []
    last_signature: Optional[Tuple[int, int, int]] = None

    for attempt in range(max(1, retries)):
        before = file_signature(path)
        if before is None:
            raise FileNotFoundError(path)

        last_lines = read_lines(path, encoding)
        after = file_signature(path)
        last_signature = after

        if before == after:
            return last_lines, after

        if attempt + 1 < max(1, retries):
            time.sleep(max(0.0, retry_delay))

    return last_lines, last_signature


def read_initial_tail(path: Path, n: int, encoding: str) -> Tuple[List[str], List[str]]:
    """Return (full_snapshot, initial_tail)."""
    lines = read_lines(path, encoding)
    if n == 0:
        return lines, []
    return lines, lines[-n:]


def file_signature(path: Path) -> Optional[Tuple[int, int, int]]:
    """Return (mtime_ns, size, inode-ish identity), or None if absent."""
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size, getattr(st, "st_ino", 0))
    except FileNotFoundError:
        return None


def wait_until_quiet(
    path: Path,
    first_signature: Optional[Tuple[int, int, int]],
    debounce: float,
    max_wait: float,
    keys: KeyReader,
    key_handler=None,
) -> Tuple[Optional[Tuple[int, int, int]], bool]:
    """Coalesce a burst of writes. Return (stable_signature, quit_requested)."""
    if debounce <= 0:
        return file_signature(path), False

    deadline = time.monotonic() + max_wait
    previous = first_signature

    while time.monotonic() < deadline:
        if sleep_responsive(debounce, keys, key_handler):
            return previous, True
        current = file_signature(path)
        if current == previous:
            return current, False
        previous = current

    return previous, False


def _line_identity(line: str) -> str:
    """Canonical identity used for structural diff matching.

    Line terminators are deliberately ignored. A writer commonly turns an
    unterminated final line into a terminated line immediately before
    appending more text, and Windows tooling may rewrite LF as CRLF. Neither
    should make htail treat the whole document as structurally different.
    """
    return line.rstrip("\r\n")


def compute_changes(
    old: Sequence[str],
    new: Sequence[str],
) -> Tuple[List[Tuple[str, List[str]]], int, int, int]:
    """Compute position-anchored line changes between two snapshots.

    A whole-document SequenceMatcher is unsafe for log-like documents with
    repeated blocks. It can align a newly appended ``Result / Verification /
    Message`` section with an older, similar section and therefore report only
    the few unique lines as new. htail instead locks the unchanged prefix and
    suffix first and runs SequenceMatcher *only inside the changed window*.

    This also makes the common case robust when the previous last line merely
    gains a newline before a new block is appended.
    """
    old_keys = [_line_identity(line) for line in old]
    new_keys = [_line_identity(line) for line in new]

    # Lock the unchanged prefix by position. Ignoring only the line terminator
    # keeps LF/CRLF changes and final-newline completion from defeating the
    # append path.
    prefix = 0
    prefix_limit = min(len(old), len(new))
    while prefix < prefix_limit and old_keys[prefix] == new_keys[prefix]:
        prefix += 1

    # Perfect append (including the case where the former final line only
    # gained its terminating newline): every suffix line is fresh output.
    if prefix == len(old) and len(new) >= len(old):
        appended = list(new[prefix:])
        events = [("add", appended)] if appended else []
        return events, len(appended), 0, 0

    # Lock an unchanged suffix as well, without allowing it to overlap the
    # prefix. This constrains repeated-text matching to the true edit window.
    suffix = 0
    old_remaining = len(old) - prefix
    new_remaining = len(new) - prefix
    suffix_limit = min(old_remaining, new_remaining)
    while (
        suffix < suffix_limit
        and old_keys[len(old) - 1 - suffix] == new_keys[len(new) - 1 - suffix]
    ):
        suffix += 1

    old_end = len(old) - suffix if suffix else len(old)
    new_end = len(new) - suffix if suffix else len(new)
    old_mid = list(old[prefix:old_end])
    new_mid = list(new[prefix:new_end])
    old_mid_keys = old_keys[prefix:old_end]
    new_mid_keys = new_keys[prefix:new_end]

    # If the changed window reaches the current end of file, preserve the
    # whole fresh tail as one event instead of asking SequenceMatcher to find
    # similarities inside it. This is the semantic htail wants for logs and
    # coordination files: a newly written/re-written tail block must be shown
    # in full, even when its Result/Verification/Message boilerplate resembles
    # an older block.
    if suffix == 0:
        events: List[Tuple[str, List[str]]] = []
        deleted_count = len(old_mid)
        if old_mid:
            events.append(("delete", old_mid))
        if new_mid:
            if old_mid:
                events.append(("replace", new_mid))
                return events, 0, len(new_mid), deleted_count
            events.append(("add", new_mid))
            return events, len(new_mid), 0, 0
        return events, 0, 0, deleted_count

    matcher = difflib.SequenceMatcher(a=old_mid_keys, b=new_mid_keys, autojunk=False)
    events: List[Tuple[str, List[str]]] = []
    added_count = 0
    replaced_count = 0
    deleted_count = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        if tag == "insert":
            lines = new_mid[j1:j2]
            if lines:
                events.append(("add", lines))
                added_count += len(lines)

        elif tag == "delete":
            lines = old_mid[i1:i2]
            if lines:
                events.append(("delete", lines))
                deleted_count += len(lines)

        elif tag == "replace":
            old_lines = old_mid[i1:i2]
            new_lines = new_mid[j1:j2]
            if old_lines:
                events.append(("delete", old_lines))
                deleted_count += len(old_lines)
            if new_lines:
                events.append(("replace", new_lines))
                replaced_count += len(new_lines)

    return events, added_count, replaced_count, deleted_count


# ---------------------------------------------------------------------------
# Syntax highlighting
# ---------------------------------------------------------------------------


class SyntaxHighlighter:
    """Terminal renderer for Markdown plus Pygments highlighting for code/text files."""

    MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd", ".mkdn"}

    def __init__(self, path: Path, requested: str, color: bool) -> None:
        self.path = path
        self.requested = requested
        self.color = color
        self.mode = "none"
        self.syntax_name = "none"
        self.lexer = None
        self.formatter = None
        self.warning: Optional[str] = None

        if not color or requested.lower() == "none":
            return

        name = requested.lower()
        is_markdown = (
            path.suffix.lower() in self.MARKDOWN_SUFFIXES
            or name in ("markdown", "md")
        )

        # Markdown is rendered, rather than merely colouring its source
        # punctuation. Pygments is still used inside fenced code blocks when
        # available, so Markdown logs remain readable while embedded code gets
        # real syntax highlighting.
        if name == "auto" and path.suffix.lower() in self.MARKDOWN_SUFFIXES:
            self.mode = "markdown-rendered"
            self.syntax_name = "Markdown (rendered)"
            if not HAVE_PYGMENTS:
                self.warning = (
                    "Pygments unavailable; Markdown rendering works, but fenced "
                    "code blocks use plain terminal styling"
                )
            return

        if name in ("markdown", "md"):
            self.mode = "markdown-rendered"
            self.syntax_name = "Markdown (rendered)"
            if not HAVE_PYGMENTS:
                self.warning = (
                    "Pygments unavailable; Markdown rendering works, but fenced "
                    "code blocks use plain terminal styling"
                )
            return

        if HAVE_PYGMENTS:
            try:
                if name == "auto":
                    self.lexer = get_lexer_for_filename(
                        path.name, stripnl=False, ensurenl=False
                    )
                else:
                    self.lexer = get_lexer_by_name(
                        requested, stripnl=False, ensurenl=False
                    )
                self.formatter = Terminal256Formatter()
                self.mode = "pygments"
                self.syntax_name = getattr(self.lexer, "name", requested)
                return
            except ClassNotFound:
                if name != "auto":
                    self.warning = (
                        f"unknown syntax lexer '{requested}'; syntax highlighting disabled"
                    )

        # If auto-detection failed and this still looks like Markdown, render it.
        if is_markdown:
            self.mode = "markdown-rendered"
            self.syntax_name = "Markdown (rendered)"

    @property
    def enabled(self) -> bool:
        return self.mode != "none"

    def render(self, lines: Sequence[str]) -> str:
        if not lines:
            return ""
        return "\n".join(self.render_lines(lines))

    def render_lines(self, lines: Sequence[str]) -> List[str]:
        """Return one ANSI-rendered string per logical input line."""
        if not lines:
            return []

        if self.mode == "markdown-rendered":
            return self._render_markdown_lines(lines)

        if self.mode == "pygments" and self.lexer is not None and self.formatter is not None:
            text = "".join(lines)
            rendered = pygments_highlight(text, self.lexer, self.formatter)
            pieces = rendered.splitlines()
        else:
            pieces = [line.rstrip("\r\n") for line in lines]

        # Formatters should preserve line count, but keep the UI robust if a
        # lexer/formatter does something surprising.
        if len(pieces) < len(lines):
            pieces.extend([""] * (len(lines) - len(pieces)))
        elif len(pieces) > len(lines):
            pieces = pieces[: len(lines)]

        return pieces

    def _render_markdown_lines(self, lines: Sequence[str]) -> List[str]:
        rendered: List[str] = []
        in_fence = False
        fence_marker = ""
        fence_lexer = None
        code_formatter = Terminal256Formatter() if HAVE_PYGMENTS else None

        for raw in lines:
            body = raw.rstrip("\r\n")
            fence = re.match(r"^(\s*)(```|~~~)\s*([^\s`]*)?.*$", body)

            if fence:
                indent, marker, language = fence.groups()
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                    fence_lexer = None
                    language = (language or "").strip()
                    if HAVE_PYGMENTS and language:
                        try:
                            fence_lexer = get_lexer_by_name(
                                language, stripnl=False, ensurenl=False
                            )
                        except ClassNotFound:
                            fence_lexer = None
                    label = f" code: {language}" if language else " code"
                    rendered.append(
                        f"{indent}{DIM}┌─{label}{RESET}" if self.color else f"{indent}┌─{label}"
                    )
                elif marker == fence_marker:
                    in_fence = False
                    fence_marker = ""
                    fence_lexer = None
                    rendered.append(
                        f"{indent}{DIM}└─{RESET}" if self.color else f"{indent}└─"
                    )
                else:
                    rendered.append(body)
                continue

            if in_fence:
                code = body
                if fence_lexer is not None and code_formatter is not None:
                    styled = pygments_highlight(code, fence_lexer, code_formatter).rstrip("\r\n")
                else:
                    styled = paint(code, MAGENTA, self.color)
                rendered.append("  " + styled)
                continue

            rendered.append(self._render_markdown_line(body))

        return rendered

    def _render_markdown_line(self, body: str) -> str:
        # Horizontal rules become actual terminal separators.
        if re.match(r"^\s{0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})$", body):
            return paint("────────────────────────────────────────", DIM, self.color)

        heading = re.match(r"^(\s*)(#{1,6})\s+(.*)$", body)
        if heading:
            indent, hashes, rest = heading.groups()
            level = len(hashes)
            marker = "▌ " if level <= 2 else "› "
            style = BOLD_LIGHT_CYAN if level <= 2 else CYAN
            return f"{indent}{style}{BOLD}{marker}{self._inline_md(rest)}{RESET}"

        quote = re.match(r"^(\s*)>+\s?(.*)$", body)
        if quote:
            indent, rest = quote.groups()
            return f"{indent}{GREEN}│{RESET} {self._inline_md(rest)}"

        task = re.match(r"^(\s*)[-+*]\s+\[([ xX])\]\s+(.*)$", body)
        if task:
            indent, state, rest = task.groups()
            mark = "☑" if state.lower() == "x" else "☐"
            return f"{indent}{YELLOW}{mark}{RESET} {self._inline_md(rest)}"

        bullet = re.match(r"^(\s*)[-+*]\s+(.*)$", body)
        if bullet:
            indent, rest = bullet.groups()
            return f"{indent}{YELLOW}•{RESET} {self._inline_md(rest)}"

        numbered = re.match(r"^(\s*)(\d+[.)])\s+(.*)$", body)
        if numbered:
            indent, marker, rest = numbered.groups()
            return f"{indent}{YELLOW}{marker}{RESET} {self._inline_md(rest)}"

        return self._inline_md(body)

    def _inline_md(self, text: str) -> str:
        """Render common inline Markdown while keeping the text itself intact."""
        if not text:
            return text

        # Protect inline-code spans first so later emphasis regexes do not
        # interpret punctuation inside code.
        placeholders: List[str] = []

        def stash(value: str) -> str:
            placeholders.append(value)
            return f"\x00{len(placeholders)-1}\x00"

        def code_repl(match: re.Match[str]) -> str:
            content = match.group(1)
            return stash(paint(content, MAGENTA, self.color))

        text = re.sub(r"`([^`]+)`", code_repl, text)

        def link_repl(match: re.Match[str]) -> str:
            label, url = match.groups()
            styled = paint(label, CYAN + UNDERLINE, self.color)
            # Preserve the destination without letting it dominate the line.
            return stash(f"{styled} {paint('(' + url + ')', DIM, self.color)}")

        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_repl, text)

        text = re.sub(
            r"\*\*([^*]+)\*\*|__([^_]+)__",
            lambda m: paint(m.group(1) or m.group(2), BOLD, self.color),
            text,
        )
        text = re.sub(
            r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)",
            lambda m: paint(m.group(1) or m.group(2), ITALIC, self.color),
            text,
        )

        for idx, value in enumerate(placeholders):
            text = text.replace(f"\x00{idx}\x00", value)
        return text


# ---------------------------------------------------------------------------
# Self-update support (GitHub Releases)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    asset_url: str
    asset_name: str
    checksum_url: Optional[str] = None
    notes: str = ""
    runtime_url: Optional[str] = None
    runtime_checksum_url: Optional[str] = None
    runtime_abi: Optional[str] = None


def _clean_release_note_line(text: str) -> str:
    """Reduce simple Markdown in release notes to compact terminal text."""
    text = text.strip()
    text = re.sub(r"^[-*+]\s+", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.strip()


def release_note_sections(notes: str) -> Tuple[List[str], List[str], List[str]]:
    """Return categorized (features, bug fixes, other) GitHub release notes."""
    features: List[str] = []
    fixes: List[str] = []
    other: List[str] = []
    section: Optional[str] = None
    for raw in notes.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", line)
        if heading:
            name = heading.group(1).strip().lower()
            if "feature" in name or "enhancement" in name or name == "new":
                section = "features"
            elif "bug" in name or name in {"fixes", "fix"}:
                section = "fixes"
            else:
                section = None
            continue
        if re.match(r"^[-*+]\s+", line):
            item = _clean_release_note_line(line)
            if section == "features":
                features.append(item)
            elif section == "fixes":
                fixes.append(item)
            else:
                other.append(item)
    return features, fixes, other


def _version_key(value: str) -> Tuple[int, ...]:
    """Return a conservative numeric version key for tags like v0.7.1."""
    value = value.strip().lstrip("vV")
    match = re.match(r"^(\d+(?:\.\d+)*)", value)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(candidate: str, current: str) -> bool:
    candidate_key = _version_key(candidate)
    current_key = _version_key(current)
    return bool(candidate_key and current_key and candidate_key > current_key)


def current_cpython_abi() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def runtime_cache_dir(runtime_id: str, abi: str) -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "htail"
    return root / "runtime" / runtime_id / abi


def _runtime_id_from_source(source: str) -> Optional[str]:
    match = re.search(r'^HTAIL_RUNTIME_ID\s*=\s*"([0-9a-fA-F]{64})"', source, re.MULTILINE)
    return match.group(1).lower() if match else None


def _install_runtime_bundle(content: bytes, target: Path, runtime_id: str, abi: str, report) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as outer:
        manifest = json.loads(outer.read("runtime.json").decode("utf-8"))
        if manifest.get("runtime_id") != runtime_id:
            raise RuntimeError("runtime bundle id does not match htail core")
        if manifest.get("abi") != abi:
            raise RuntimeError(f"runtime bundle ABI {manifest.get('abi')!r} does not match {abi}")
        payloads = []
        total = 0
        for wheel_name in manifest.get("wheels") or []:
            payload = outer.read("wheels/" + wheel_name)
            with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
                total += sum(item.file_size for item in wheel.infolist() if not item.is_dir())
            payloads.append(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{abi}-", dir=str(target.parent)))
    current = 0
    try:
        report(f"Unpacking runtime {abi}…", current, total)
        for payload in payloads:
            with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
                for item in wheel.infolist():
                    if item.is_dir():
                        continue
                    destination = (temp / item.filename).resolve()
                    if temp.resolve() not in destination.parents:
                        raise RuntimeError(f"unsafe runtime path: {item.filename}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with wheel.open(item) as src, destination.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    mode = (item.external_attr >> 16) & 0o777
                    if mode:
                        os.chmod(destination, mode)
                    current += item.file_size
                    report(f"Unpacking runtime {abi}…", current, total)
        try:
            os.replace(temp, target)
        except OSError:
            if not target.is_dir():
                raise
    finally:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)


class UpdateService:
    """Check GitHub Releases in the background and install a confirmed update."""

    def __init__(self, repo: str, asset_name: str = DEFAULT_UPDATE_ASSET) -> None:
        self.repo = repo.strip()
        self.asset_name = asset_name.strip() or DEFAULT_UPDATE_ASSET
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._release: Optional[ReleaseInfo] = None
        self._error: Optional[str] = None
        self._done = False

    @property
    def enabled(self) -> bool:
        return bool(self.repo and "/" in self.repo)

    def start(self, force: bool = False) -> bool:
        """Start a background release check. Return True if one was started."""
        if not self.enabled:
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            if self._done and not force:
                return False
            self._done = False
            self._error = None
            if force:
                self._release = None
            self._thread = threading.Thread(
                target=self._check_worker,
                name="htail-update-check",
                daemon=True,
            )
            thread = self._thread
        thread.start()
        return True

    def refresh(self) -> bool:
        """Force a fresh GitHub release check unless one is already running."""
        return self.start(force=True)

    def snapshot(self) -> Tuple[bool, Optional[ReleaseInfo], Optional[str]]:
        with self._lock:
            return self._done, self._release, self._error

    def _check_worker(self) -> None:
        release: Optional[ReleaseInfo] = None
        error: Optional[str] = None
        try:
            release = self.check_latest()
        except Exception as exc:  # background check must never break following
            error = str(exc)
        with self._lock:
            self._release = release
            self._error = error
            self._done = True

    def check_latest(self) -> Optional[ReleaseInfo]:
        if not self.enabled:
            return None

        api_url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        request = urllib.request.Request(
            api_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"htail/{HTAIL_VERSION}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RuntimeError(f"update check failed: GitHub returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"update check failed: {exc.reason}") from exc

        tag = str(payload.get("tag_name") or "").strip()
        version = tag.lstrip("vV")
        notes = str(payload.get("body") or "")
        if not tag or not is_newer_version(version, HTAIL_VERSION):
            return None

        assets = payload.get("assets") or []
        asset_url: Optional[str] = None
        checksum_url: Optional[str] = None
        runtime_abi = current_cpython_abi()
        runtime_name = f"htail-runtime-{runtime_abi}.zip"
        runtime_url: Optional[str] = None
        runtime_checksum_url: Optional[str] = None
        for asset in assets:
            name = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "")
            if name == self.asset_name and url:
                asset_url = url
            elif name in (f"{self.asset_name}.sha256", f"{self.asset_name}.sha256sum") and url:
                checksum_url = url
            elif name == runtime_name and url:
                runtime_url = url
            elif name in (f"{runtime_name}.sha256", f"{runtime_name}.sha256sum") and url:
                runtime_checksum_url = url

        if not asset_url:
            raise RuntimeError(
                f"release {tag} has no '{self.asset_name}' asset"
            )
        if not checksum_url:
            raise RuntimeError(
                f"release {tag} has no '{self.asset_name}.sha256' checksum asset"
            )

        return ReleaseInfo(
            version=version,
            tag=tag,
            asset_url=asset_url,
            asset_name=self.asset_name,
            checksum_url=checksum_url,
            notes=notes,
            runtime_url=runtime_url,
            runtime_checksum_url=runtime_checksum_url,
            runtime_abi=runtime_abi,
        )

    def install(
        self,
        release: ReleaseInfo,
        target: Path,
        progress: Optional[Callable[[str, Optional[int], Optional[int]], None]] = None,
    ) -> Tuple[bool, str]:
        """Download, validate and atomically replace the running script.

        ``progress`` receives ``(stage, current_bytes, total_bytes)``. Byte
        counts are supplied while downloading; later stages use ``None``.
        """
        target = target.resolve()
        target_dir = target.parent
        if not target.exists():
            return False, f"cannot locate running script: {target}"
        if not os.access(target, os.W_OK):
            return False, f"running script is not writable: {target}"

        def report(stage: str, current: Optional[int] = None, total: Optional[int] = None) -> None:
            if progress is None:
                return
            try:
                progress(stage, current, total)
            except Exception:
                pass

        temp_path: Optional[Path] = None
        try:
            request = urllib.request.Request(release.asset_url, headers={"User-Agent": f"htail/{HTAIL_VERSION}"})
            with urllib.request.urlopen(request, timeout=15.0) as response:
                headers = getattr(response, "headers", {})
                size = headers.get("Content-Length") if hasattr(headers, "get") else None
                total = int(size) if size and size.isdigit() else None
                chunks: List[bytes] = []
                current = 0
                report("Downloading release…", current, total)
                while True:
                    try:
                        chunk = response.read(65536)
                    except TypeError:
                        # Simple test doubles and a few file-like adapters only
                        # implement read() without a size argument. Real HTTP
                        # responses still take the streaming path above.
                        chunk = response.read()
                        if chunk:
                            chunks.append(chunk)
                            current += len(chunk)
                            report("Downloading release…", current, total)
                        break
                    if not chunk:
                        break
                    chunks.append(chunk)
                    current += len(chunk)
                    report("Downloading release…", current, total)
                content = b"".join(chunks)

            report("Verifying release SHA-256 checksum…")
            expected_sha256: Optional[str] = None
            if release.checksum_url:
                checksum_request = urllib.request.Request(release.checksum_url, headers={"User-Agent": f"htail/{HTAIL_VERSION}"})
                with urllib.request.urlopen(checksum_request, timeout=10.0) as response:
                    checksum_text = response.read().decode("utf-8", errors="replace")
                checksum_match = re.search(r"\b([0-9a-fA-F]{64})\b", checksum_text)
                if not checksum_match:
                    return False, "release checksum asset does not contain a SHA-256 digest"
                expected_sha256 = checksum_match.group(1).lower()

            actual_sha256 = hashlib.sha256(content).hexdigest()
            if expected_sha256 and actual_sha256 != expected_sha256:
                return False, "downloaded update failed SHA-256 verification"

            try:
                source = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                return False, f"downloaded update is not UTF-8 text: {exc}"
            if not source.startswith("#!/"):
                return False, "downloaded update does not look like an executable htail script"
            if f'HTAIL_VERSION = "{release.version}"' not in source:
                return False, f"downloaded script does not identify itself as version {release.version}"
            try:
                compile(source, str(target), "exec")
            except SyntaxError as exc:
                return False, f"downloaded update failed syntax validation: {exc}"

            runtime_id = _runtime_id_from_source(source)
            if runtime_id:
                abi = release.runtime_abi or current_cpython_abi()
                runtime_target = runtime_cache_dir(runtime_id, abi)
                if runtime_target.is_dir():
                    report(f"Runtime already prepared ({abi})…")
                else:
                    if not release.runtime_url or not release.runtime_checksum_url:
                        return False, f"release {release.tag} has no runtime asset for {abi}"
                    runtime_request = urllib.request.Request(release.runtime_url, headers={"User-Agent": f"htail/{HTAIL_VERSION}"})
                    with urllib.request.urlopen(runtime_request, timeout=20.0) as response:
                        headers = getattr(response, "headers", {})
                        size = headers.get("Content-Length") if hasattr(headers, "get") else None
                        runtime_total = int(size) if size and size.isdigit() else None
                        runtime_chunks = []
                        runtime_current = 0
                        report(f"Downloading runtime {abi}…", runtime_current, runtime_total)
                        while True:
                            try:
                                chunk = response.read(65536)
                            except TypeError:
                                chunk = response.read()
                            if not chunk:
                                break
                            runtime_chunks.append(chunk)
                            runtime_current += len(chunk)
                            report(f"Downloading runtime {abi}…", runtime_current, runtime_total)
                        runtime_content = b"".join(runtime_chunks)
                    report(f"Verifying runtime {abi}…")
                    checksum_request = urllib.request.Request(release.runtime_checksum_url, headers={"User-Agent": f"htail/{HTAIL_VERSION}"})
                    with urllib.request.urlopen(checksum_request, timeout=10.0) as response:
                        checksum_text = response.read().decode("utf-8", errors="replace")
                    checksum_match = re.search(r"\b([0-9a-fA-F]{64})\b", checksum_text)
                    if not checksum_match:
                        return False, "runtime checksum asset does not contain a SHA-256 digest"
                    if hashlib.sha256(runtime_content).hexdigest() != checksum_match.group(1).lower():
                        return False, "downloaded runtime failed SHA-256 verification"
                    _install_runtime_bundle(runtime_content, runtime_target, runtime_id, abi, report)

            report("Preparing update…")
            fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.update-", dir=str(target_dir))
            temp_path = Path(temp_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, target.stat().st_mode)

            report("Unpacking application…")
            prepared = subprocess.run(
                [sys.executable, str(temp_path), "--prepare-core"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20.0,
                check=False,
            )
            if prepared.returncode != 0:
                detail = prepared.stderr.strip() or f"exit code {prepared.returncode}"
                return False, f"could not prepare updated application: {detail}"

            report("Backing up current executable…")
            backup = target.with_name(target.name + ".bak")
            shutil.copy2(target, backup)
            report("Installing update…")
            os.replace(temp_path, target)
            temp_path = None

            # Do not report success merely because the wrapper was replaced.
            # Launch the installed wrapper from the same inherited environment
            # as an interactive restart and require both wrapper and bundled
            # application verification before considering the update complete.
            report("Verifying installed application…")
            verification_error: Optional[str] = None
            try:
                version_check = subprocess.run(
                    [sys.executable, str(target), "--version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=20.0,
                    check=False,
                )
                expected_version = f"htail {release.version}"
                if version_check.returncode != 0 or version_check.stdout.strip() != expected_version:
                    detail = (
                        version_check.stderr.strip()
                        or version_check.stdout.strip()
                        or f"exit code {version_check.returncode}"
                    )
                    verification_error = f"version check failed: {detail}"
                else:
                    bundle_check = subprocess.run(
                        [sys.executable, str(target), "--bundle-self-test"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=20.0,
                        check=False,
                    )
                    if bundle_check.returncode != 0:
                        detail = (
                            bundle_check.stderr.strip()
                            or bundle_check.stdout.strip()
                            or f"exit code {bundle_check.returncode}"
                        )
                        verification_error = f"bundle self-test failed: {detail}"
            except Exception as exc:
                verification_error = str(exc)

            if verification_error is not None:
                try:
                    shutil.copy2(backup, target)
                except OSError as restore_exc:
                    return False, (
                        f"installed update failed verification ({verification_error}); "
                        f"backup restore also failed: {restore_exc}"
                    )
                return False, f"installed update failed verification ({verification_error}); restored backup"

            return True, f"updated {target.name} {HTAIL_VERSION} → {release.version}" + (" (SHA-256 verified)" if expected_sha256 else "")
        except urllib.error.URLError as exc:
            return False, f"update download failed: {exc.reason}"
        except OSError as exc:
            return False, f"could not install update: {exc}"
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Self-install support
# ---------------------------------------------------------------------------


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve() or os.path.samefile(a, b)
    except (OSError, FileNotFoundError):
        return a.resolve() == b.resolve()


def _load_app_state() -> dict:
    try:
        return json.loads(APP_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _save_app_state(state: dict) -> None:
    try:
        APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temp = APP_STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, APP_STATE_FILE)
    except OSError:
        # Installation should never fail merely because preference persistence
        # is unavailable.
        pass


def _path_entries() -> List[Path]:
    entries: List[Path] = []
    for raw in os.environ.get("PATH", "").split(os.pathsep):
        if raw:
            try:
                entries.append(Path(raw).expanduser().resolve())
            except OSError:
                entries.append(Path(raw).expanduser())
    return entries


def _command_collision(name: str, source: Path) -> Optional[Path]:
    found = shutil.which(name)
    if found:
        found_path = Path(found)
        if not _same_file(found_path, source):
            return found_path

    target = Path.home() / ".local" / "bin" / name
    if target.exists() and not _same_file(target, source):
        return target
    return None


def choose_install_name(source: Path, preferred: str = DEFAULT_INSTALL_COMMAND) -> Tuple[Optional[str], Optional[Path]]:
    """Choose a non-conflicting short command name."""
    collision = _command_collision(preferred, source)
    if collision is None:
        return preferred, None

    for alternative in ("htail", "hlog"):
        if _command_collision(alternative, source) is None:
            return alternative, collision
    return None, collision


def _ensure_local_bin_path() -> Tuple[bool, str]:
    """Persist ~/.local/bin in the user's shell PATH when necessary."""
    local_bin = (Path.home() / ".local" / "bin").resolve()
    if local_bin in _path_entries():
        return True, "~/.local/bin is already in PATH"

    shell = Path(os.environ.get("SHELL", "")).name.lower()
    export_line = 'export PATH="$HOME/.local/bin:$PATH"'

    if shell in ("bash", "zsh"):
        rc = Path.home() / (".zshrc" if shell == "zsh" else ".bashrc")
        try:
            existing = rc.read_text(encoding="utf-8") if rc.exists() else ""
            if export_line not in existing:
                block = (
                    "\n# Added by htail\n"
                    + export_line
                    + "\n"
                )
                with rc.open("a", encoding="utf-8") as handle:
                    handle.write(block)
            return True, f"added ~/.local/bin to PATH in {rc} (effective in new shells)"
        except OSError as exc:
            return False, f"could not update {rc}: {exc}"

    if shell == "fish":
        conf = Path.home() / ".config" / "fish" / "conf.d" / "htail-path.fish"
        try:
            conf.parent.mkdir(parents=True, exist_ok=True)
            conf.write_text(
                "# Added by htail\nfish_add_path $HOME/.local/bin\n",
                encoding="utf-8",
            )
            return True, f"added ~/.local/bin to PATH via {conf} (effective in new shells)"
        except OSError as exc:
            return False, f"could not update {conf}: {exc}"

    return False, (
        "~/.local/bin is not currently in PATH and this shell could not be configured automatically; "
        'add export PATH="$HOME/.local/bin:$PATH" to your shell profile'
    )


def install_self(command_name: str, *, interactive: bool = True) -> Tuple[bool, str, Path]:
    """Install this script into ~/.local/bin under `command_name`."""
    source = Path(__file__).resolve()
    local_bin = Path.home() / ".local" / "bin"
    target = local_bin / command_name

    collision = _command_collision(command_name, source)
    if collision is not None:
        return False, f"'{command_name}' already resolves to {collision}; nothing was overwritten", target

    try:
        local_bin.mkdir(parents=True, exist_ok=True)
        content = source.read_bytes()
        fd, temp_name = tempfile.mkstemp(prefix=f".{command_name}.install-", dir=str(local_bin))
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            mode = source.stat().st_mode | 0o111
            os.chmod(temp, mode)
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)
    except OSError as exc:
        return False, f"could not install {command_name}: {exc}", target

    path_ok, path_message = _ensure_local_bin_path()
    message = f"installed as {target}. {path_message}"
    if not path_ok:
        message += ". The command will not be globally available until PATH is updated."
    return True, message, target


def maybe_offer_self_install(args: argparse.Namespace, color: bool) -> None:
    """Offer one-time user installation before entering cbreak/full-screen mode."""
    if getattr(args, "install", None) is not None:
        requested = args.install or DEFAULT_INSTALL_COMMAND
        ok, message, _ = install_self(requested)
        stream = sys.stdout if ok else sys.stderr
        print(paint(f"[htail] {message}", GREEN if ok else BOLD_YELLOW, color), file=stream)
        raise SystemExit(0 if ok else 1)

    if getattr(args, "no_self_install_prompt", False):
        return
    if getattr(args, "check_update", False) or getattr(args, "update", False):
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return

    source = Path(__file__).resolve()
    # If the preferred command already points at this exact script, installation
    # is complete and no first-run prompt is needed.
    existing = shutil.which(DEFAULT_INSTALL_COMMAND)
    if existing and _same_file(Path(existing), source):
        return

    state = _load_app_state()
    if state.get("self_install_prompted"):
        return

    proposed, collision = choose_install_name(source)
    state["self_install_prompted"] = True
    _save_app_state(state)

    if proposed is None:
        print(
            paint(
                "[htail] Could not find a free install command among ht, htail, and hlog. "
                "Use --install NAME to choose one explicitly.",
                BOLD_YELLOW,
                color,
            )
        )
        return

    if collision is not None:
        print(
            paint(
                f"[htail] '{DEFAULT_INSTALL_COMMAND}' already resolves to {collision}; it will not be overwritten.",
                BOLD_YELLOW,
                color,
            )
        )
        prompt = f"Install this tool as '{proposed}' in ~/.local/bin instead? [Y/n] "
    else:
        print("[htail] This tool is currently running as a local script.")
        prompt = (
            f"Install it as '{proposed}' in ~/.local/bin so you can run it from anywhere? [Y/n] "
        )

    print(prompt, end="", flush=True)
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if answer not in ("", "y", "yes"):
        print("[htail] installation skipped. You can run --install later.")
        return

    ok, message, target = install_self(proposed)
    print(paint(f"[htail] {message}", GREEN if ok else BOLD_YELLOW, color))
    if ok:
        # The parent shell's environment cannot be mutated by a child process.
        # If ~/.local/bin was newly added to a shell rc file, the command is
        # available automatically in new shells.
        if (Path.home() / ".local" / "bin").resolve() not in _path_entries():
            print(f"[htail] Open a new shell to use: {proposed} <file>")
        else:
            print(f"[htail] Installed command: {proposed} <file>")


class RestartRequested(Exception):
    """Raised after a successful self-update so terminal state can unwind first."""

    def __init__(self, target: Path, argv: Sequence[str], message: str) -> None:
        super().__init__(message)
        self.target = target
        self.argv = list(argv)
        self.message = message


# ---------------------------------------------------------------------------
# Display filtering
# ---------------------------------------------------------------------------


@dataclass
class DisplayFilter:
    include: Optional[Pattern[str]] = None
    exclude: Optional[Pattern[str]] = None

    @property
    def active(self) -> bool:
        return self.include is not None or self.exclude is not None

    def accepts(self, line: str) -> bool:
        text = line.rstrip("\r\n")
        if self.include is not None and self.include.search(text) is None:
            return False
        if self.exclude is not None and self.exclude.search(text) is not None:
            return False
        return True

    def apply_events(
        self, events: Sequence[Tuple[str, List[str]]]
    ) -> Tuple[List[Tuple[str, List[str]]], int]:
        filtered: List[Tuple[str, List[str]]] = []
        visible = 0
        for kind, lines in events:
            kept = [line for line in lines if self.accepts(line)]
            if kept:
                filtered.append((kind, kept))
                visible += len(kept)
        return filtered, visible


def compile_display_filter(args: argparse.Namespace) -> DisplayFilter:
    flags = re.IGNORECASE if args.ignore_case else 0
    try:
        include = re.compile(args.grep, flags) if args.grep else None
        exclude = re.compile(args.exclude, flags) if args.exclude else None
    except re.error as exc:
        raise ValueError(f"invalid filter regex: {exc}") from exc
    return DisplayFilter(include=include, exclude=exclude)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def format_update_header(
    update_number: int,
    added: int,
    replaced: int,
    deleted: int,
    elapsed: Optional[float],
    visible_lines: int,
    total_changed_lines: int,
    filter_active: bool,
    color: bool,
) -> str:
    now = datetime.now().astimezone()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S %Z").rstrip()

    parts = [f"update {update_number}", stamp]
    if added:
        parts.append(f"+{added} line{'s' if added != 1 else ''}")
    if replaced:
        parts.append(f"{replaced} replaced")
    if deleted:
        parts.append(f"-{deleted} deleted")
    if filter_active and visible_lines != total_changed_lines:
        parts.append(f"{visible_lines}/{total_changed_lines} shown")
    if elapsed is not None:
        parts.append(f"{elapsed:.1f}s since previous update")

    return paint("── " + " · ".join(parts) + " ──", DIM, color)


def render_initial_lines(
    lines: Sequence[str], highlighter: SyntaxHighlighter
) -> List[str]:
    safe_lines = [sanitize_source_line(line) for line in lines]
    if highlighter.enabled:
        return highlighter.render_lines(safe_lines)
    return [line.rstrip("\r\n") for line in safe_lines]


def render_event_lines(
    events: Sequence[Tuple[str, List[str]]],
    highlighter: SyntaxHighlighter,
    color: bool,
    show_deletions: bool,
    mark_replacements: bool,
) -> List[str]:
    rendered: List[str] = []

    for kind, lines in events:
        if kind == "delete" and not show_deletions:
            continue

        if kind == "delete":
            for line in lines:
                rendered.append(
                    paint(
                        "- " + sanitize_source_line(line).rstrip("\r\n"),
                        BOLD_RED,
                        color,
                    )
                )
            continue

        safe_lines = [sanitize_source_line(line) for line in lines]
        raw_lines = [line.rstrip("\r\n") for line in safe_lines]

        if highlighter.enabled:
            styled_lines = highlighter.render_lines(safe_lines)
            marker = "~ " if kind == "replace" and mark_replacements else "▌ "
            marker = paint(marker, BOLD_LIGHT_CYAN, color)
            for styled in styled_lines:
                rendered.append(marker + styled)
            continue

        prefix = "~ " if kind == "replace" and mark_replacements else ""
        for line in raw_lines:
            rendered.append(paint(prefix + line, BOLD_LIGHT_CYAN, color))

    return rendered


def print_stream_update(
    header: str,
    lines: Sequence[str],
) -> None:
    sys.stdout.write("\n" + header + "\n")
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Interactive full-screen UI
# ---------------------------------------------------------------------------


@dataclass
class UpdateRecord:
    number: int
    start: int
    end: int


class TerminalUI:
    """Small terminal viewport that keeps file following and reading separate."""

    def __init__(
        self,
        path: Path,
        highlighter: SyntaxHighlighter,
        display_filter: DisplayFilter,
        color: bool,
        idle_warn: float,
        update_service: Optional[UpdateService] = None,
        update_target: Optional[Path] = None,
    ) -> None:
        self.path = path
        self.highlighter = highlighter
        self.display_filter = display_filter
        self.color = color
        self.idle_warn = idle_warn
        self.update_service = update_service
        self.update_target = update_target or Path(__file__).resolve()
        self.update_release: Optional[ReleaseInfo] = None
        self.update_check_done = False
        self.update_check_error: Optional[str] = None
        self.update_confirm_active = False
        self.update_installing = False
        self.update_manual_check_pending = False
        self.last_update_check_monotonic = time.monotonic()

        self.lines: List[str] = []
        self.updates: List[UpdateRecord] = []
        self.top = 0
        self._layout_dirty = True
        self._layout_width: Optional[int] = None
        self._visual_lines: List[str] = []
        self._logical_to_visual: List[int] = []
        self._visual_to_logical: List[int] = []
        self.paused = False
        self.unseen_updates = 0
        self.last_update_monotonic: Optional[float] = None
        self.watch_started_monotonic = time.monotonic()
        self.idle_warned = False
        self.last_status_second: Optional[int] = None
        self.message: Optional[str] = None
        self.message_until = 0.0
        self.active = False
        self.help_active = False

    def __enter__(self) -> "TerminalUI":
        self.active = True
        sys.stdout.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR_SCREEN + CURSOR_HOME)
        sys.stdout.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.active = False
        sys.stdout.write(SHOW_CURSOR + RESET + ALT_SCREEN_OFF)
        sys.stdout.flush()

    def dimensions(self) -> Tuple[int, int]:
        size = shutil.get_terminal_size((100, 30))
        return max(20, size.columns), max(4, size.lines)

    def content_width(self) -> int:
        # Keep the terminal's final physical column unused. Filling it can set
        # an implicit-wrap flag, causing the next newline to consume an extra
        # row and letting the fixed footer overwrite body content.
        return max(1, self.dimensions()[0] - 1)

    def body_height(self) -> int:
        width, height = self.dimensions()
        return max(1, height - self.footer_height(width))

    def footer_height(self, width: Optional[int] = None) -> int:
        if width is None:
            width = self.dimensions()[0]
        # Two lines keep the controls readable on normal terminals. Very
        # narrow terminals collapse to a compact one-line status; ? opens the
        # complete command reference.
        return 1 if width < 72 else 2

    def _mark_layout_dirty(self) -> None:
        self._layout_dirty = True

    def _ensure_layout(self, width: int) -> None:
        if not self._layout_dirty and self._layout_width == width:
            return

        self._layout_width = width
        self._layout_dirty = False
        self._visual_lines = []
        self._logical_to_visual = []
        self._visual_to_logical = []

        for logical_index, line in enumerate(self.lines):
            self._logical_to_visual.append(len(self._visual_lines))
            wrapped = wrap_ansi(line, width)
            if not wrapped:
                wrapped = [""]
            self._visual_lines.extend(wrapped)
            self._visual_to_logical.extend([logical_index] * len(wrapped))

    def _logical_start_to_visual(self, logical_index: int, width: Optional[int] = None) -> int:
        if width is None:
            width = self.content_width()
        self._ensure_layout(width)
        if logical_index < 0 or logical_index >= len(self._logical_to_visual):
            return 0
        return self._logical_to_visual[logical_index]

    def _current_logical_index(self, width: Optional[int] = None) -> int:
        if width is None:
            width = self.content_width()
        self._ensure_layout(width)
        if not self._visual_to_logical:
            return 0
        index = min(max(0, self.top), len(self._visual_to_logical) - 1)
        return self._visual_to_logical[index]

    def max_scroll_top(self) -> int:
        width = self.content_width()
        self._ensure_layout(width)
        # Allow a header near the end of the buffer to sit at the TOP of the
        # viewport, leaving blank rows below it. This is central to htail's
        # "open the freshest update at its beginning" behavior.
        return max(0, len(self._visual_lines) - 1)

    def bottom_top(self) -> int:
        width = self.content_width()
        self._ensure_layout(width)
        return max(0, len(self._visual_lines) - self.body_height())

    def set_message(self, text: str, duration: float = 2.5) -> None:
        self.message = text
        self.message_until = time.monotonic() + duration

    def add_initial(self, raw_lines: Sequence[str]) -> None:
        visible = [line for line in raw_lines if self.display_filter.accepts(line)]
        self.lines.extend(render_initial_lines(visible, self.highlighter))
        self._mark_layout_dirty()
        self.top = self.bottom_top()
        self.render()

    def add_update(
        self,
        update_number: int,
        events: Sequence[Tuple[str, List[str]]],
        added: int,
        replaced: int,
        deleted: int,
        elapsed: Optional[float],
        show_deletions: bool,
        mark_replacements: bool,
        now_monotonic: float,
    ) -> None:
        filtered_events, visible_count = self.display_filter.apply_events(events)

        # Hidden deletions should not count as displayable lines in a filter
        # summary, even though the underlying diff state still tracks them.
        if not show_deletions:
            visible_count -= sum(
                len(lines) for kind, lines in filtered_events if kind == "delete"
            )
            visible_count = max(0, visible_count)

        total_changed = added + replaced + (deleted if show_deletions else 0)

        if self.lines:
            self.lines.append("")
        header_index = len(self.lines)
        self.lines.append(
            format_update_header(
                update_number=update_number,
                added=added,
                replaced=replaced,
                deleted=deleted if show_deletions else 0,
                elapsed=elapsed,
                visible_lines=visible_count,
                total_changed_lines=total_changed,
                filter_active=self.display_filter.active,
                color=self.color,
            )
        )

        rendered = render_event_lines(
            filtered_events,
            highlighter=self.highlighter,
            color=self.color,
            show_deletions=show_deletions,
            mark_replacements=mark_replacements,
        )

        if not rendered and self.display_filter.active:
            rendered = [paint("  (no changed lines matched the active filter)", DIM, self.color)]

        self.lines.extend(rendered)
        self._mark_layout_dirty()
        end_index = max(header_index, len(self.lines) - 1)
        self.updates.append(UpdateRecord(update_number, header_index, end_index))

        self.last_update_monotonic = now_monotonic
        self.idle_warned = False

        if self.paused:
            self.unseen_updates += 1
        else:
            # Deliberately jump to the START of each freshest update, never to
            # its tail. A subsequent update replaces this viewport anchor with
            # the newer update's header.
            self.top = self._logical_start_to_visual(header_index)
            self.unseen_updates = 0

        self.render()

    def add_system_line(self, text: str, warning: bool = False) -> None:
        if self.lines:
            self.lines.append("")
        style = BOLD_YELLOW if warning else DIM
        self.lines.append(paint(f"[htail] {text}", style, self.color))
        self._mark_layout_dirty()
        # System notices do not steal the viewport from the update currently
        # being read.
        self.render()

    def clear_display(self) -> None:
        self.lines.clear()
        self.updates.clear()
        self._mark_layout_dirty()
        self.top = 0
        self.unseen_updates = 0
        self.set_message("display cleared; file tracking continues")
        self.render()

    def freshest(self) -> None:
        if self.updates:
            self.top = self._logical_start_to_visual(self.updates[-1].start)
            self.unseen_updates = 0
            self.render()

    def previous_update(self) -> None:
        if not self.updates:
            return
        current_logical = self._current_logical_index()
        candidates = [u for u in self.updates if u.start < current_logical]
        target = candidates[-1] if candidates else self.updates[0]
        self.top = self._logical_start_to_visual(target.start)
        self.render()

    def next_update(self) -> None:
        if not self.updates:
            return
        current_logical = self._current_logical_index()
        candidates = [u for u in self.updates if u.start > current_logical]
        target = candidates[0] if candidates else self.updates[-1]
        self.top = self._logical_start_to_visual(target.start)
        self.render()

    def handle_key(self, key: str) -> bool:
        """Handle one key. Return True only when the caller should quit."""
        if self.update_confirm_active:
            if key in ("n", "N", "q", "Q"):
                self.update_confirm_active = False
                self.set_message("update cancelled")
                self.render()
                return False
            if key in ("y", "Y") and self.update_release is not None and self.update_service is not None:
                self.update_installing = True
                self.render()
                ok, message = self.update_service.install(self.update_release, self.update_target)
                self.update_installing = False
                if ok:
                    raise RestartRequested(self.update_target, sys.argv[1:], message)
                self.update_confirm_active = False
                self.set_message(message, duration=6.0)
                self.render()
                return False
            return False

        if key in ("q", "Q"):
            return True

        if key in ("u", "U"):
            if self.update_release is not None:
                self.update_confirm_active = True
                self.render()
            elif self.update_service is None or not self.update_service.enabled:
                self.set_message("update repository is not configured yet")
                self.render()
            elif not self.update_check_done:
                self.set_message("checking GitHub for updates…")
                self.render()
            else:
                self.update_manual_check_pending = True
                if self.update_service.refresh():
                    self.update_check_done = False
                    self.update_check_error = None
                    self.last_update_check_monotonic = time.monotonic()
                    self.set_message("checking GitHub for updates…")
                else:
                    self.set_message("update check already in progress")
                self.render()
            return False

        if key == "?":
            self.help_active = not self.help_active
            self.render()
            return False

        if self.help_active:
            # Keep the help screen stable. Only ?, q/Q are meaningful while
            # it is open; file changes are still captured in the background.
            return False

        if key in ("p", "P"):
            self.paused = not self.paused
            if self.paused:
                self.set_message("viewport paused; changes are still being captured")
            else:
                if self.updates:
                    self.top = self._logical_start_to_visual(self.updates[-1].start)
                self.unseen_updates = 0
                self.set_message("resumed at freshest update")
            self.render()
            return False

        if key in ("c", "C"):
            self.clear_display()
            return False

        if key in ("f", "F"):
            self.freshest()
            return False

        if key == "[":
            self.previous_update()
            return False

        if key == "]":
            self.next_update()
            return False

        old_top = self.top
        page = max(1, self.body_height() - 2)

        if key == "UP":
            self.top -= 1
        elif key == "DOWN":
            self.top += 1
        elif key == "PAGEUP":
            self.top -= page
        elif key == "PAGEDOWN":
            self.top += page
        elif key == "HOME":
            self.top = 0
        elif key == "END":
            self.top = self.bottom_top()
        else:
            return False

        self.top = min(max(0, self.top), self.max_scroll_top())
        if self.top != old_top:
            self.render()
        return False

    def idle_seconds(self, now: Optional[float] = None) -> float:
        now = time.monotonic() if now is None else now
        base = (
            self.last_update_monotonic
            if self.last_update_monotonic is not None
            else self.watch_started_monotonic
        )
        return max(0.0, now - base)

    def tick(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now

        if self.update_service is not None and self.update_service.enabled:
            # Long-lived viewers re-check releases hourly. Manual `u` checks are
            # immediate, so users never have to restart htail to discover a release.
            if (
                self.update_release is None
                and self.update_check_done
                and now - self.last_update_check_monotonic >= AUTO_UPDATE_CHECK_INTERVAL
            ):
                if self.update_service.refresh():
                    self.update_check_done = False
                    self.update_check_error = None
                    self.last_update_check_monotonic = now

            done, release, error = self.update_service.snapshot()
            changed = (
                done != self.update_check_done
                or release != self.update_release
                or error != self.update_check_error
            )
            self.update_check_done = done
            self.update_release = release
            self.update_check_error = error
            if changed and done:
                self.last_update_check_monotonic = now
                if release is not None:
                    self.update_manual_check_pending = False
                    self.set_message(f"update {release.version} available — press u", duration=5.0)
                    self.render()
                elif self.update_manual_check_pending:
                    self.update_manual_check_pending = False
                    if error:
                        self.set_message(f"update check failed: {error}", duration=5.0)
                    else:
                        self.set_message("already on the latest release", duration=3.0)
                    self.render()

        idle = self.idle_seconds(now)

        if self.idle_warn > 0 and idle >= self.idle_warn and not self.idle_warned:
            self.idle_warned = True
            self.add_system_line(
                f"idle for {format_duration(idle)} (warning threshold {format_duration(self.idle_warn)})",
                warning=True,
            )

        # Refresh only when the displayed whole-second idle value changes.
        second = int(idle)
        if second != self.last_status_second:
            self.last_status_second = second
            self.render()

    def _current_update_number(self) -> Optional[int]:
        current_logical = self._current_logical_index()
        current: Optional[int] = None
        for update in self.updates:
            if update.start <= current_logical:
                current = update.number
            else:
                break
        return current

    def _position_summary(self, body_height: int) -> Tuple[int, int]:
        width = self.content_width()
        self._ensure_layout(width)
        below = max(0, len(self._visual_lines) - (self.top + body_height))
        above = self.top
        return above, below

    def _status_lines(self, width: int, body_height: int) -> List[str]:
        now = time.monotonic()
        if self.message and now <= self.message_until:
            lead = self.message
        else:
            self.message = None
            lead = "PAUSED" if self.paused else "LIVE"

        above, below = self._position_summary(body_height)
        current = self._current_update_number()
        idle = self.idle_seconds(now)
        idle_text = f"idle {format_duration(idle)}"
        if self.idle_warn > 0 and idle >= self.idle_warn:
            idle_text = f"⚠ {idle_text}"

        syntax_display = self.highlighter.syntax_name.replace(" (rendered)", "")

        if width < 72:
            syntax = syntax_display
            if syntax.lower().startswith("markdown"):
                syntax = "MD"
            elif len(syntax) > 8:
                syntax = syntax[:8]

            parts = [f"htail {HTAIL_VERSION}", syntax, lead]
            if current is not None:
                parts.append(f"U{current}")
            if above:
                parts.append(f"↑{above}")
            if below:
                parts.append(f"↓{below}")
            if self.paused and self.unseen_updates:
                parts.append(f"+{self.unseen_updates} new")
            if self.update_release is not None:
                parts.append(f"UPDATE {self.update_release.version}")
            parts.append(idle_text)
            parts.append("? help")
            return [" · ".join(parts)]

        top_parts = [f"htail {HTAIL_VERSION}", syntax_display, lead]

        if current is not None:
            top_parts.append(f"update {current}")

        if self.paused and self.unseen_updates:
            top_parts.append(
                f"{self.unseen_updates} new update{'s' if self.unseen_updates != 1 else ''} captured"
            )

        if above:
            top_parts.append(f"↑{above}")
        if below:
            top_parts.append(f"↓{below}")
        if self.update_release is not None:
            top_parts.append(f"UPDATE {self.update_release.version}")
        top_parts.append(idle_text)

        if self.update_release is not None:
            update_control = " · u update"
        elif self.update_service is not None and self.update_service.enabled:
            update_control = " · u check"
        else:
            update_control = ""
        if width >= 108:
            controls = "↑↓/Pg scroll · [/] update · f newest · p pause · c clear" + update_control + " · q quit · ? help"
        else:
            controls = "↑↓/Pg · [/] update · f newest · p pause · c clear" + update_control + " · q quit · ? help"

        return [" · ".join(top_parts), controls]

    def _help_lines(self, width: int, body_height: int) -> List[str]:
        rows = [
            f"htail {HTAIL_VERSION} — keyboard help",
            "",
            "Navigation",
            "  ↑ / ↓       scroll one line",
            "  PgUp/PgDn   scroll one page",
            "  Home / End  first line / bottom of displayed history",
            "  [ / ]       previous / next captured update",
            "  f           jump to the newest update header",
            "",
            "Display",
            "  p           pause/resume automatic viewport jumps",
            "              (updates are still captured while paused)",
            "  c           clear displayed history; tracking continues",
            "  u           check for updates / install an available release",
            "  ?           close this help",
            "  q           quit htail",
            "",
            "Status",
            "  ↑N / ↓N     rendered lines above / below the viewport",
            "  update N    update batch currently being viewed",
            "  idle Ns     time since the last detected file change",
            "",
            "Long lines are soft-wrapped to the terminal width.",
            "",
            "New updates open at their first line. Pause with p if you want",
            "to keep reading older content without the viewport moving.",
        ]
        return [clip_ansi(line, width) for line in rows[:body_height]]

    def _panel_lines(
        self, title: str, content: Sequence[str], width: int, body_height: int
    ) -> List[str]:
        """Render a centered bordered panel inside the terminal viewport."""
        from .terminal_cells import display_width

        if width < 34:
            return [clip_ansi(line, width) for line in content[:body_height]]
        panel_width = min(88, max(34, width - 6))
        inner_width = panel_width - 4
        rendered: List[str] = []
        for line in content:
            rendered.extend(wrap_ansi(line, inner_width) if line else [""])
        limit = max(1, body_height - 2)
        if len(rendered) > limit:
            rendered = rendered[: limit - 1] + [paint("… more release notes omitted", DIM, self.color)]
        label = f" {title} "
        dashes = max(0, panel_width - 2 - display_width(label))
        top = "╭" + "─" * (dashes // 2) + label + "─" * (dashes - dashes // 2) + "╮"
        bottom = "╰" + "─" * (panel_width - 2) + "╯"
        if self.color:
            top = paint(top, BOLD_LIGHT_CYAN, True)
            bottom = paint(bottom, BOLD_LIGHT_CYAN, True)
        panel = [top]
        for line in rendered:
            padded = line + " " * max(0, inner_width - display_width(line))
            panel.append(f"{paint('│', CYAN, self.color)} {padded} {paint('│', CYAN, self.color)}")
        panel.append(bottom)
        indent = " " * max(0, (width - panel_width) // 2)
        panel = [indent + line for line in panel]
        return ([""] * max(0, (body_height - len(panel)) // 2) + panel)[:body_height]

    def _update_modal_lines(self, width: int, body_height: int) -> List[str]:
        release = self.update_release
        if release is None:
            return self._panel_lines("Update", ["No update is currently available.", "", "Press u to check GitHub again."], width, body_height)
        if self.update_installing:
            return self._panel_lines(
                "Installing update",
                [
                    paint(f"htail {HTAIL_VERSION}  →  {release.version}", BOLD, self.color),
                    "", "Downloading release…", "Verifying SHA-256 checksum…",
                    "Replacing the executable atomically…", "",
                    "The current file will reopen automatically.",
                ],
                width, body_height,
            )
        features, fixes, other = release_note_sections(release.notes)
        content: List[str] = [
            paint(f"htail {HTAIL_VERSION}  →  {release.version}", BOLD, self.color),
            paint(self.update_service.repo if self.update_service else "", DIM, self.color),
            "",
        ]
        if features:
            content.append(paint("New features", BOLD_LIGHT_CYAN, self.color))
            content.extend(f"• {item}" for item in features[:4])
            content.append("")
        if fixes:
            content.append(paint("Bug fixes", BOLD_YELLOW, self.color))
            content.extend(f"• {item}" for item in fixes[:4])
            content.append("")
        if not features and not fixes:
            content.append(paint("Release notes", BOLD_LIGHT_CYAN, self.color))
            content.extend(f"• {item}" for item in other[:5])
            if not other:
                content.append("No categorized release notes were provided.")
            content.append("")
        content.extend([
            "A .bak copy of the current executable will be kept.",
            f"After updating, htail will reopen {self.path.name}.",
            "",
            paint("[Y] Update now", BOLD + GREEN, self.color) + "    " + paint("[N] Cancel", BOLD, self.color),
        ])
        return self._panel_lines("Update available", content, width, body_height)

    def render(self) -> None:
        if not self.active:
            return

        terminal_width, height = self.dimensions()
        width = max(1, terminal_width - 1)
        footer_height = self.footer_height(terminal_width)
        body_height = max(1, height - footer_height)
        self._ensure_layout(width)
        self.top = min(max(0, self.top), self.max_scroll_top())

        sys.stdout.write(CURSOR_HOME)
        modal_lines = self._update_modal_lines(width, body_height) if self.update_confirm_active else None
        help_lines = self._help_lines(width, body_height) if self.help_active and modal_lines is None else None
        for row in range(body_height):
            sys.stdout.write(CLEAR_LINE)
            if modal_lines is not None:
                if row < len(modal_lines):
                    sys.stdout.write(modal_lines[row])
            elif help_lines is not None:
                if row < len(help_lines):
                    sys.stdout.write(help_lines[row])
            else:
                index = self.top + row
                if index < len(self._visual_lines):
                    sys.stdout.write(self._visual_lines[index])
            if row < body_height - 1:
                sys.stdout.write("\n")

        if self.update_confirm_active:
            status_lines = ["UPDATE · y confirm · n cancel"]
            if footer_height == 2:
                status_lines.append("File watching continues while the update dialog is open")
        elif self.help_active:
            status_lines = ["HELP · ? close · q quit"]
            if footer_height == 2:
                status_lines.append("File watching continues while help is open")
        else:
            status_lines = self._status_lines(width, body_height)

        warning = self.idle_warn > 0 and self.idle_seconds() >= self.idle_warn
        style = BOLD_YELLOW + REVERSE if warning else REVERSE
        for i in range(footer_height):
            sys.stdout.write("\n" + CLEAR_LINE)
            line = status_lines[i] if i < len(status_lines) else ""
            if self.color:
                sys.stdout.write(style + clip_ansi(line, width) + RESET)
            else:
                sys.stdout.write(clip_ansi(line, width))
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="htail",
        description=(
            "Follow a text file, timestamping and highlighting every observed change. "
            "Interactive terminals open each freshest update at its first line."
        ),
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"htail {HTAIL_VERSION}",
    )
    parser.add_argument(
        "file",
        type=Path,
        nargs="?",
        help="text file to watch",
    )
    parser.add_argument(
        "--install",
        nargs="?",
        const=DEFAULT_INSTALL_COMMAND,
        metavar="NAME",
        help="install this script into ~/.local/bin (default command: ht)",
    )
    parser.add_argument(
        "--no-self-install-prompt",
        action="store_true",
        help="do not offer first-run installation into ~/.local/bin",
    )
    parser.add_argument(
        "--check-update",
        action="store_true",
        help="check GitHub Releases for a newer htail version and exit",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="install the latest GitHub release, if newer, and exit",
    )
    parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help="initial lines to display (default: 50); later updates are never capped",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=float,
        default=0.10,
        help="polling interval in seconds (default: 0.10)",
    )
    parser.add_argument(
        "--verify-interval",
        type=float,
        default=1.0,
        help=(
            "periodically verify file contents even when filesystem metadata "
            "looks unchanged (default: 1.0s; 0 disables)"
        ),
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=0.15,
        help="quiet time used to group a burst of writes (default: 0.15s)",
    )
    parser.add_argument(
        "--max-debounce",
        type=float,
        default=1.0,
        help="maximum time to wait while grouping a write burst (default: 1.0s)",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="text encoding (default: utf-8)",
    )
    parser.add_argument(
        "--syntax",
        default="auto",
        metavar="LANG",
        help=(
            "display syntax: auto, none, markdown, or a Pygments lexer name such as "
            "python/json; Markdown is terminal-rendered (default: auto)"
        ),
    )
    parser.add_argument(
        "--no-install-prompt",
        action="store_true",
        help="do not offer to install Pygments when it is missing",
    )
    parser.add_argument(
        "--show-deletions",
        action="store_true",
        help="also show removed lines in bold red",
    )
    parser.add_argument(
        "--mark-replacements",
        action="store_true",
        help="mark replacement lines with '~ ' instead of the normal change gutter",
    )
    parser.add_argument(
        "--grep",
        metavar="REGEX",
        help="show only changed lines matching this regex (tracking remains complete)",
    )
    parser.add_argument(
        "--exclude",
        metavar="REGEX",
        help="hide changed lines matching this regex (tracking remains complete)",
    )
    parser.add_argument(
        "-I",
        "--ignore-case",
        action="store_true",
        help="make --grep and --exclude case-insensitive",
    )
    parser.add_argument(
        "--idle-warn",
        type=float,
        default=300.0,
        metavar="SECONDS",
        help="warn after this many seconds without a file update; 0 disables (default: 300)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI colours and syntax highlighting",
    )
    parser.add_argument(
        "--no-start-banner",
        action="store_true",
        help="in stream mode, do not print the initial watching banner",
    )

    args = parser.parse_args()

    if args.lines < 0:
        parser.error("--lines must be >= 0")
    if args.interval <= 0:
        parser.error("--interval must be > 0")
    if args.verify_interval < 0:
        parser.error("--verify-interval must be >= 0")
    if args.debounce < 0:
        parser.error("--debounce must be >= 0")
    if args.max_debounce < 0:
        parser.error("--max-debounce must be >= 0")
    if args.idle_warn < 0:
        parser.error("--idle-warn must be >= 0")

    return args


def main() -> int:
    args = parse_args()
    enable_windows_ansi()

    color = sys.stdout.isatty() and not args.no_color

    # First-run installation is deliberately handled before the file argument:
    # the README bootstrap command launches htail with no file so it can install
    # itself as `ht`, then print normal usage.
    maybe_offer_self_install(args, color)

    update_repo = os.environ.get("HTAIL_UPDATE_REPO", DEFAULT_UPDATE_REPO).strip()
    update_service = UpdateService(update_repo)

    if args.check_update or args.update:
        try:
            release = update_service.check_latest()
        except Exception as exc:
            print(f"htail: {exc}", file=sys.stderr)
            return 1

        if release is None:
            print(f"htail {HTAIL_VERSION} is the latest published release.")
            return 0

        if args.check_update:
            print(f"htail {release.version} is available (current: {HTAIL_VERSION}).")
            return 0

        ok, message = update_service.install(release, Path(__file__).resolve())
        print(f"[htail] {message}", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    if args.file is None:
        print(f"htail {HTAIL_VERSION}")
        print("Usage: ht FILE   (or: htail FILE)")
        print("Example: ht agent-coordination.md")
        return 0

    path: Path = args.file

    try:
        display_filter = compile_display_filter(args)
    except ValueError as exc:
        print(f"htail: {exc}", file=sys.stderr)
        return 2

    # The Pygments prompt must happen before KeyReader places stdin in cbreak
    # mode and before entering the full-screen interface.
    maybe_offer_pygments_install(args, color)
    highlighter = SyntaxHighlighter(path, args.syntax, color)

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if interactive:
        update_service.start()

    if highlighter.warning and not interactive:
        print(f"[htail] {highlighter.warning}", file=sys.stderr)

    restart_request: Optional[RestartRequested] = None

    with KeyReader() as keys:
        missing_announced = False
        while not path.exists():
            if not missing_announced:
                print(
                    f"[htail] waiting for {path}"
                    + (" · press q to quit" if keys.enabled else ""),
                    file=sys.stderr if not interactive else sys.stdout,
                    flush=True,
                )
                missing_announced = True
            if sleep_responsive(args.interval, keys):
                return 0

        try:
            previous, initial_tail = read_initial_tail(path, args.lines, args.encoding)
        except OSError as exc:
            print(f"htail: cannot read {path}: {exc}", file=sys.stderr)
            return 1

        signature = file_signature(path)
        last_content_verify = time.monotonic()
        active_verify_until = 0.0
        last_update_time: Optional[float] = None
        stream_watch_started = time.monotonic()
        stream_idle_warned = False
        file_missing = False
        update_number = 0

        # Interactive and stream modes share all watcher/diff semantics. Only
        # their rendering differs.
        ui_context = (
            TerminalUI(
                path=path,
                highlighter=highlighter,
                display_filter=display_filter,
                color=color,
                idle_warn=args.idle_warn,
                update_service=update_service,
                update_target=Path(__file__).resolve(),
            )
            if interactive
            else None
        )

        if ui_context is not None:
            ui_context.__enter__()
            ui_context.add_system_line(
                f"htail {HTAIL_VERSION} · watching {path} · syntax: {highlighter.syntax_name}"
            )
            if highlighter.warning:
                ui_context.add_system_line(highlighter.warning, warning=True)
            ui_context.add_initial(initial_tail)
            key_handler = ui_context.handle_key
        else:
            key_handler = None
            if not args.no_start_banner:
                print(
                    f"[htail {HTAIL_VERSION}] watching {path} · initial context: {len(initial_tail)} line(s) "
                    f"· syntax: {highlighter.syntax_name}",
                    flush=True,
                )
            initial_visible = [
                line for line in initial_tail if display_filter.accepts(line)
            ]
            for line in render_initial_lines(initial_visible, highlighter):
                print(line)

        try:
            while True:
                if ui_context is not None:
                    ui_context.tick()
                elif args.idle_warn > 0 and not stream_idle_warned:
                    idle_base = (
                        last_update_time
                        if last_update_time is not None
                        else stream_watch_started
                    )
                    idle = time.monotonic() - idle_base
                    if idle >= args.idle_warn:
                        print(
                            f"[htail] idle for {format_duration(idle)} "
                            f"(warning threshold {format_duration(args.idle_warn)})",
                            file=sys.stderr,
                            flush=True,
                        )
                        stream_idle_warned = True

                if sleep_responsive(args.interval, keys, key_handler):
                    return 0

                now_check = time.monotonic()
                current_signature = file_signature(path)
                periodic_verify_due = (
                    args.verify_interval > 0
                    and now_check - last_content_verify >= args.verify_interval
                )
                # After any observed change, aggressively reread the actual
                # contents for a short window. Editors and agent tooling may
                # publish a file in stages with pauses longer than debounce;
                # this makes the follow-up chunks independent of mtime/size.
                active_verify_due = now_check <= active_verify_until
                verify_due = periodic_verify_due or active_verify_due

                # Fast path: normally metadata tells us that nothing changed.
                # Periodically bypass that optimization and compare the actual
                # contents as a safety net for staged writes, same-size rewrites,
                # coarse/stale mtimes, and network/shared-filesystem metadata.
                if current_signature == signature and not verify_due:
                    continue

                if current_signature is None:
                    if not file_missing:
                        if ui_context is not None:
                            ui_context.add_system_line(
                                f"{path} disappeared; waiting for it to return",
                                warning=True,
                            )
                        else:
                            print(
                                f"[htail] {path} disappeared; waiting for it to return",
                                file=sys.stderr,
                                flush=True,
                            )
                        file_missing = True
                    signature = None
                    last_content_verify = now_check
                    continue

                # Metadata-triggered changes still get the normal quiet period.
                # A periodic verification does not need to sleep unless metadata
                # itself says the file is currently changing.
                if current_signature != signature:
                    stable_signature, quit_requested = wait_until_quiet(
                        path,
                        current_signature,
                        args.debounce,
                        args.max_debounce,
                        keys,
                        key_handler,
                    )
                    if quit_requested:
                        return 0
                    if stable_signature is None:
                        signature = None
                        last_content_verify = time.monotonic()
                        continue
                else:
                    stable_signature = current_signature

                try:
                    current, verified_signature = read_verified_snapshot(
                        path, args.encoding
                    )
                except FileNotFoundError:
                    signature = None
                    last_content_verify = time.monotonic()
                    continue
                except OSError as exc:
                    if ui_context is not None:
                        ui_context.add_system_line(f"read error: {exc}", warning=True)
                    else:
                        print(f"[htail] read error: {exc}", file=sys.stderr)
                    signature = stable_signature
                    last_content_verify = time.monotonic()
                    continue

                # Prefer the signature observed around the verified read.  This
                # closes a race where a writer changes the file after debounce
                # but before/during our snapshot.
                if verified_signature is not None:
                    stable_signature = verified_signature
                last_content_verify = time.monotonic()

                events, added, replaced, deleted = compute_changes(previous, current)

                if events:
                    now_monotonic = time.monotonic()
                    elapsed = (
                        None
                        if last_update_time is None
                        else now_monotonic - last_update_time
                    )
                    update_number += 1

                    if ui_context is not None:
                        ui_context.add_update(
                            update_number=update_number,
                            events=events,
                            added=added,
                            replaced=replaced,
                            deleted=deleted,
                            elapsed=elapsed,
                            show_deletions=args.show_deletions,
                            mark_replacements=args.mark_replacements,
                            now_monotonic=now_monotonic,
                        )
                    else:
                        filtered_events, visible_count = display_filter.apply_events(events)
                        if not args.show_deletions:
                            visible_count -= sum(
                                len(lines)
                                for kind, lines in filtered_events
                                if kind == "delete"
                            )
                            visible_count = max(0, visible_count)
                        total_changed = added + replaced + (
                            deleted if args.show_deletions else 0
                        )
                        header = format_update_header(
                            update_number=update_number,
                            added=added,
                            replaced=replaced,
                            deleted=deleted if args.show_deletions else 0,
                            elapsed=elapsed,
                            visible_lines=visible_count,
                            total_changed_lines=total_changed,
                            filter_active=display_filter.active,
                            color=color,
                        )
                        rendered = render_event_lines(
                            filtered_events,
                            highlighter=highlighter,
                            color=color,
                            show_deletions=args.show_deletions,
                            mark_replacements=args.mark_replacements,
                        )
                        if not rendered and display_filter.active:
                            rendered = ["  (no changed lines matched the active filter)"]
                        print_stream_update(header, rendered)

                    last_update_time = now_monotonic
                    stream_idle_warned = False
                    active_verify_until = max(
                        active_verify_until, now_monotonic + ACTIVE_VERIFY_WINDOW
                    )

                previous = current
                signature = stable_signature

                if file_missing:
                    file_missing = False
                    if ui_context is not None:
                        ui_context.add_system_line(f"resumed {path}")
                    else:
                        print(f"[htail] resumed {path}", file=sys.stderr, flush=True)

        except RestartRequested as exc:
            restart_request = exc
        except KeyboardInterrupt:
            return 0
        finally:
            if ui_context is not None:
                ui_context.__exit__(None, None, None)
                if restart_request is None:
                    print(
                        f"[htail] stopped {path} after {update_number} update"
                        f"{'s' if update_number != 1 else ''}.",
                        flush=True,
                    )

    if restart_request is not None:
        print(f"[htail] {restart_request.message}; reopening {path}", flush=True)
        os.execv(
            sys.executable,
            [sys.executable, str(restart_request.target), *restart_request.argv],
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
