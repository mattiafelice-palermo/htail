"""ANSI-aware terminal-cell geometry for rendered htail rows.

The renderer stores Python strings, but terminals lay them out in cells. This
module is the single geometry seam used by clipping, wrapping, horizontal
slicing, tab expansion, and pane padding. ANSI/OSC sequences are zero-width;
Unicode widths come from the maintained :mod:`wcwidth` package when bundled.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterator, List, Optional, Tuple

from . import core

try:  # The release bundle pins wcwidth; the fallback keeps source tests usable.
    from wcwidth import wcwidth as _wcwidth
    from wcwidth import wcswidth as _wcswidth
except ImportError:  # pragma: no cover - exercised only in dependency-less trees
    _wcwidth = None
    _wcswidth = None


TAB_SIZE = 8


def _fallback_char_width(char: str) -> int:
    if char in "\r\n\x00" or unicodedata.category(char).startswith("C"):
        return 0
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in ("W", "F"):
        return 2
    if 0x1F300 <= ord(char) <= 0x1FAFF:
        return 2
    return 1


def _char_width(char: str) -> int:
    if not char:
        return 0
    if _wcwidth is not None:
        return max(0, int(_wcwidth(char)))
    return _fallback_char_width(char)


def _plain_width(text: str) -> int:
    if not text:
        return 0
    if _wcswidth is not None:
        width = int(_wcswidth(text))
        if width >= 0:
            return width
    return sum(_char_width(char) for char in text)


def _iter_units(text: str) -> Iterator[Tuple[bool, str]]:
    """Yield ``(is_ansi, value)`` units without treating ANSI as content."""

    pos = 0
    while pos < len(text):
        match = core.ANSI_RE.match(text, pos)
        if match is not None:
            yield True, match.group(0)
            pos = match.end()
        else:
            yield False, text[pos]
            pos += 1


def display_width(text: str) -> int:
    """Return the terminal-cell width of ANSI-styled Unicode text."""

    if text.isascii() and "\x1b" not in text:
        if not any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
            return len(text)
        return sum(1 for char in text if ord(char) >= 0x20 and ord(char) != 0x7F)

    width = 0
    plain: List[str] = []
    for is_ansi, value in _iter_units(text):
        if is_ansi:
            if plain:
                width += _plain_width("".join(plain))
                plain.clear()
        else:
            plain.append(value)
    if plain:
        width += _plain_width("".join(plain))
    return width


def _safe_plain_char(char: str) -> str:
    """Defensively make a non-ANSI unit visible before it reaches stdout."""

    codepoint = ord(char)
    if char == "\t":
        return char
    if char == "\n":
        return "␊"
    if char == "\r":
        return "␍"
    if codepoint < 0x20 or 0x7F <= codepoint <= 0x9F:
        if codepoint == 0x00:
            return "␀"
        if codepoint == 0x07:
            return "␇"
        if codepoint == 0x08:
            return "␈"
        if codepoint == 0x1B:
            return "␛"
        if codepoint == 0x7F:
            return "␡"
        if 0x80 <= codepoint <= 0x9F:
            return f"\\x{codepoint:02x}"
        return chr(0x2400 + codepoint)
    return char


def clip_cells_ansi(text: str, width: int) -> str:
    """Clip ANSI-styled text to ``width`` terminal cells."""

    if width <= 0:
        return ""
    visible = 0
    out: List[str] = []
    for is_ansi, value in _iter_units(text):
        if is_ansi:
            out.append(value)
            continue
        safe = _safe_plain_char(value)
        char_width = _char_width(safe)
        if char_width and visible + char_width > width:
            break
        out.append(safe)
        visible += char_width
    if "\x1b]8;;" in text:
        out.append("\x1b]8;;\x1b\\")
    out.append(core.RESET)
    return "".join(out)


def slice_cells_ansi(text: str, start: int, width: int) -> str:
    """Return the cell range ``[start, start + width)`` from styled text."""

    if width <= 0:
        return ""
    start = max(0, int(start))
    if start >= display_width(text):
        return ""
    end = start + max(0, int(width))
    visible = 0
    started = start == 0
    out: List[str] = []
    skipped_sgr: List[str] = []

    for is_ansi, value in _iter_units(text):
        if is_ansi:
            if started:
                out.append(value)
            elif value.endswith("m"):
                skipped_sgr.append(value)
            continue

        safe = _safe_plain_char(value)
        char_width = _char_width(safe)
        if visible < start:
            visible += char_width
            continue
        if visible >= end:
            break
        if not started:
            out.extend(skipped_sgr)
            skipped_sgr.clear()
            started = True
        if char_width and visible + char_width > end:
            break
        out.append(safe)
        visible += char_width

    if not started:
        return "".join(out) + core.RESET
    if "\x1b]8;;" in text:
        out.append("\x1b]8;;\x1b\\")
    out.append(core.RESET)
    return "".join(out)


def pad_cells_ansi(text: str, width: int) -> str:
    clipped = clip_cells_ansi(text, max(0, width))
    visible = display_width(clipped)
    if visible < width:
        clipped += " " * (width - visible)
    return clipped


def visible_prefix_cells_ansi(text: str, cells: int) -> str:
    """Return the first terminal cells while preserving embedded ANSI."""

    if cells <= 0:
        return ""
    out: List[str] = []
    visible = 0
    for is_ansi, value in _iter_units(text):
        if is_ansi:
            out.append(value)
            continue
        safe = _safe_plain_char(value)
        char_width = _char_width(safe)
        if char_width and visible + char_width > cells:
            break
        out.append(safe)
        visible += char_width
    return "".join(out)


def _wrap_ansi_cells(text: str, width: int) -> List[str]:
    if width <= 0:
        return [""]

    plain_text = core.strip_ansi(text)
    if display_width(text) <= width:
        return [text + core.RESET]

    change_marker_visible = ""
    change_marker_ansi = ""
    body_text = plain_text
    if plain_text.startswith("▌ ") or plain_text.startswith("~ "):
        change_marker_visible = plain_text[:2]
        change_marker_ansi = visible_prefix_cells_ansi(text, display_width(change_marker_visible))
        if core.ANSI_RE.search(change_marker_ansi) and not change_marker_ansi.endswith(core.RESET):
            change_marker_ansi += core.RESET
        body_text = plain_text[2:]

    body_hanging_indent = 0
    list_match = re.match(r"^(?:\s*)(?:[•☐☑]|\d+[.)])\s+", body_text)
    if list_match:
        body_hanging_indent = display_width(body_text[: list_match.end()])

    continuation_prefix = change_marker_ansi + (" " * body_hanging_indent)
    continuation_visible_width = display_width(change_marker_visible) + body_hanging_indent

    lines: List[str] = []
    active = ""
    current: List[str] = [active]
    visible = 0
    last_space_index: Optional[int] = None
    last_space_visible = 0

    def emit_break() -> None:
        nonlocal current, visible, last_space_index, last_space_visible
        if last_space_index is not None and last_space_visible >= max(1, width // 2):
            segment = "".join(current[:last_space_index]).rstrip()
            remainder = "".join(current[last_space_index:]).lstrip(" \t")
            lines.append(segment + core.RESET)
            current = [continuation_prefix, active] if continuation_visible_width else [active]
            if remainder:
                current.append(remainder)
                visible = continuation_visible_width + display_width(remainder)
            else:
                visible = continuation_visible_width
        else:
            lines.append("".join(current) + core.RESET)
            current = [continuation_prefix, active] if continuation_visible_width else [active]
            visible = continuation_visible_width
        last_space_index = None
        last_space_visible = 0

    i = 0
    while i < len(text):
        match = core.ANSI_RE.match(text, i)
        if match:
            code = match.group(0)
            current.append(code)
            if code.endswith("m"):
                if code == core.RESET:
                    active = ""
                else:
                    active += code
            i = match.end()
            continue

        char = _safe_plain_char(text[i])
        char_width = _char_width(char)
        if char_width and visible > 0 and visible + char_width > width:
            emit_break()
        current.append(char)
        visible += char_width
        if char in (" ", "\t"):
            last_space_index = len(current)
            last_space_visible = visible
        i += 1

        if visible >= width:
            emit_break()

    tail = "".join(current)
    if tail == active:
        tail = ""
    lines.append(tail + core.RESET)
    return lines


def expand_tabs_ansi(text: str, tabsize: int = TAB_SIZE) -> str:
    """Expand tabs to cell-local tab stops while preserving ANSI sequences."""

    if "\t" not in text:
        return text
    tabsize = max(1, int(tabsize))
    out: List[str] = []
    column = 0
    for is_ansi, value in _iter_units(text):
        if is_ansi:
            out.append(value)
            continue
        if value == "\t":
            spaces = tabsize - (column % tabsize)
            out.append(" " * spaces)
            column += spaces
        else:
            safe = _safe_plain_char(value)
            out.append(safe)
            column += _char_width(safe)
    return "".join(out)


def install() -> None:
    from .pane import Pane

    if getattr(Pane, "_htail_terminal_cells_extension", False):
        return

    original_wrap_cached = Pane._wrap_cached
    original_viewport_row = Pane._viewport_row

    def wrap_cached(self, text: str, width: int):
        return original_wrap_cached(self, expand_tabs_ansi(text), width)

    def viewport_row(self, row: str, width: int) -> str:
        return original_viewport_row(self, expand_tabs_ansi(row), width)

    Pane._wrap_cached = wrap_cached
    Pane._viewport_row = viewport_row
    core.clip_ansi = clip_cells_ansi
    core.wrap_ansi = _wrap_ansi_cells
    core.visible_prefix_ansi = visible_prefix_cells_ansi
    Pane._htail_terminal_cells_extension = True
