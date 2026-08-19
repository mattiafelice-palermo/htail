"""ANSI-aware terminal-cell geometry for rendered htail rows.

The renderer stores Python strings, but terminals lay them out in cells. This
module is the single geometry seam used by clipping, wrapping, horizontal
slicing, tab expansion, and pane padding. ANSI/OSC sequences are zero-width;
Unicode widths come from the maintained :mod:`wcwidth` package when bundled.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterator, List, Optional, Sequence, Tuple

from . import core

try:  # The release bundle pins wcwidth; the fallback keeps source tests usable.
    from wcwidth import wcwidth as _wcwidth
    from wcwidth import wcswidth as _wcswidth
except ImportError:  # pragma: no cover - exercised only in dependency-less trees
    _wcwidth = None
    _wcswidth = None


TAB_SIZE = 8
_GRAPHEME_FORMAT_CONTROLS = {0x200C, 0x200D}


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


def _is_extend(char: str) -> bool:
    codepoint = ord(char)
    category = unicodedata.category(char)
    return (
        unicodedata.combining(char) != 0
        or category in ("Mn", "Me")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _cluster_end(text: str, start: int) -> int:
    """Return the end of one wcwidth-compatible plain-text cluster."""

    end = start + 1
    first = text[start]
    if 0x1F1E6 <= ord(first) <= 0x1F1FF and end < len(text):
        if 0x1F1E6 <= ord(text[end]) <= 0x1F1FF:
            end += 1
    while end < len(text) and _is_extend(text[end]):
        end += 1
    while end < len(text) and text[end] == "\u200d":
        end += 1
        if end >= len(text):
            break
        end += 1
        while end < len(text) and _is_extend(text[end]):
            end += 1
    return end


def _iter_units(text: str) -> Iterator[Tuple[bool, str]]:
    """Yield ANSI sequences and sequence-aware plain-text units."""

    pos = 0
    while pos < len(text):
        match = core.ANSI_RE.match(text, pos)
        if match is not None:
            yield True, match.group(0)
            pos = match.end()
        else:
            end = _cluster_end(text, pos)
            yield False, text[pos:end]
            pos = end


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
    category = unicodedata.category(char)
    if char == "\t":
        return char
    if char == "\n":
        return "␊"
    if char == "\r":
        return "␍"
    if codepoint in _GRAPHEME_FORMAT_CONTROLS:
        return char
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
    if category in {"Cf", "Cn", "Cs", "Zl", "Zp"}:
        if codepoint <= 0xFFFF:
            return f"\\u{codepoint:04x}"
        return f"\\U{codepoint:08x}"
    return char


def _safe_plain_text(text: str) -> str:
    return "".join(_safe_plain_char(char) for char in text)


def cell_boundaries(text: str) -> List[int]:
    """Return terminal-cell offsets for every plain-text code-point boundary."""

    boundaries = [0] * (len(text) + 1)
    visible = 0
    pos = 0
    while pos < len(text):
        end = _cluster_end(text, pos)
        safe = _safe_plain_text(text[pos:end])
        width = _plain_width(safe)
        boundaries[pos] = visible
        for boundary in range(pos + 1, end):
            boundaries[boundary] = visible + width
        visible += width
        boundaries[end] = visible
        pos = end
    return boundaries


def cell_index_at(text: str, cell: int, *, include_partial: bool = False) -> int:
    """Return a plain-text index for a terminal-cell position.

    Mouse coordinates can land on either cell of a wide grapheme.  They must
    resolve to the same whole unit instead of being treated as a code-point
    offset into that unit.
    """

    target = max(0, int(cell))
    visible = 0
    pos = 0
    while pos < len(text):
        end = _cluster_end(text, pos)
        unit_width = _plain_width(_safe_plain_text(text[pos:end]))
        if target <= visible:
            return pos
        if unit_width and target < visible + unit_width:
            return end if include_partial else pos
        visible += unit_width
        pos = end
    return len(text)


def cell_range_bounds(text: str, start: int, end: int) -> Tuple[int, int]:
    """Return plain-text code-point bounds covering a cell range.

    A range touching any part of a grapheme includes that whole grapheme, so
    selection and styling never split wide, combining, or ZWJ sequences.
    """

    start = max(0, int(start))
    end = max(start, int(end))
    if not text or end <= start:
        return 0, 0

    visible = 0
    first: Optional[int] = None
    last: Optional[int] = None
    pos = 0
    while pos < len(text):
        next_pos = _cluster_end(text, pos)
        unit_width = _plain_width(_safe_plain_text(text[pos:next_pos]))
        unit_end = visible + unit_width
        if unit_width and unit_end > start and visible < end:
            if first is None:
                first = pos
            last = next_pos
        visible = unit_end
        pos = next_pos
        if visible >= end:
            break

    if first is None or last is None:
        return 0, 0
    return first, last


def cell_slice_bounds(text: str, start: int, width: int) -> Tuple[int, int]:
    """Return code-point bounds for a whole-cluster cell slice of plain text."""

    if width <= 0 or not text:
        return 0, 0
    start = max(0, int(start))
    end_cell = start + max(0, int(width))
    visible = 0
    first: Optional[int] = None
    last: Optional[int] = None
    pos = 0
    while pos < len(text):
        end = _cluster_end(text, pos)
        safe = _safe_plain_text(text[pos:end])
        cluster_width = _plain_width(safe)
        cluster_end_cell = visible + cluster_width
        if cluster_end_cell > start and visible < end_cell:
            if visible >= start and cluster_end_cell <= end_cell:
                if first is None:
                    first = pos
                last = end
            elif first is not None:
                break
        if visible >= end_cell:
            break
        visible = cluster_end_cell
        pos = end
    if first is None or last is None:
        return 0, 0
    return first, last


def cell_unit_bounds(text: str, *, from_end: bool = False) -> Tuple[int, int]:
    """Return the code-point bounds of the first or last plain-text cell unit."""

    if not text:
        return 0, 0
    if not from_end:
        return 0, _cluster_end(text, 0)
    end = len(text)
    start = 0
    while start < end:
        next_start = _cluster_end(text, start)
        if next_start >= end:
            return start, end
        start = next_start
    return 0, 0


def cell_chunks(text: str, width: int) -> List[Tuple[int, int]]:
    """Split plain text into whole-cell-unit code-point ranges."""

    if not text:
        return [(0, 0)]
    if width <= 0:
        return [(0, len(text))]
    chunks: List[Tuple[int, int]] = []
    line_start = 0
    line_width = 0
    pos = 0
    while pos < len(text):
        end = _cluster_end(text, pos)
        unit_width = _plain_width(_safe_plain_text(text[pos:end]))
        if pos > line_start and line_width and line_width + unit_width > width:
            chunks.append((line_start, pos))
            line_start = pos
            line_width = 0
        line_width += unit_width
        pos = end
    if line_start < len(text) or not chunks:
        chunks.append((line_start, len(text)))
    return chunks


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
        safe = _safe_plain_text(value)
        unit_width = _plain_width(safe)
        if unit_width and visible + unit_width > width:
            break
        out.append(safe)
        visible += unit_width
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

        safe = _safe_plain_text(value)
        unit_width = _plain_width(safe)
        if visible < start:
            visible += unit_width
            continue
        if visible >= end:
            break
        if not started:
            out.extend(skipped_sgr)
            skipped_sgr.clear()
            started = True
        if unit_width and visible + unit_width > end:
            break
        out.append(safe)
        visible += unit_width

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
        safe = _safe_plain_text(value)
        unit_width = _plain_width(safe)
        if unit_width and visible + unit_width > cells:
            break
        out.append(safe)
        visible += unit_width
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

        # This loop is intentionally sequence-aware: wcwidth's whole-cluster
        # result must be the same value used by clipping and measurement.
        end = _cluster_end(text, i)
        char = _safe_plain_text(text[i:end])
        char_width = _plain_width(char)
        if char_width and visible > 0 and visible + char_width > width:
            emit_break()
        current.append(char)
        visible += char_width
        if char in (" ", "\t"):
            last_space_index = len(current)
            last_space_visible = visible
        i = end

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
            safe = _safe_plain_text(value)
            out.append(safe)
            column += _plain_width(safe)
    return "".join(out)


def expand_tabs_ansi_with_boundaries(
    text: str, boundaries: Sequence[int], tabsize: int = TAB_SIZE
) -> Tuple[str, Tuple[int, ...]]:
    """Expand plain text tabs and translate source boundaries to the result."""

    if len(boundaries) != len(text) + 1:
        raise ValueError("boundaries must contain one entry per text boundary")
    tabsize = max(1, int(tabsize))
    out: List[str] = []
    expanded_boundaries = [0] * (len(text) + 1)
    output_length = 0
    column = 0
    pos = 0
    while pos < len(text):
        end = _cluster_end(text, pos)
        expanded_boundaries[pos] = output_length
        value = text[pos:end]
        if value == "\t":
            spaces = tabsize - (column % tabsize)
            safe = " " * spaces
            column += spaces
        else:
            safe = _safe_plain_text(value)
            column += _plain_width(safe)
        out.append(safe)
        output_length += len(safe)
        output_end = output_length
        for boundary in range(pos + 1, end + 1):
            expanded_boundaries[boundary] = output_end
        pos = end
    expanded_boundaries[len(text)] = output_length
    return "".join(out), tuple(expanded_boundaries[offset] for offset in boundaries)


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
