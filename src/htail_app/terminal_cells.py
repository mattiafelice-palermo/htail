"""Terminal-cell normalization for pane content.

Literal tabs are expanded before pane wrapping, horizontal slicing, clipping,
and padding. Leaving tabs in rendered rows makes terminal geometry depend on
the physical cursor column, while htail's layout code otherwise counts one
Python character per pane cell. Expanding them at the pane boundary keeps the
existing renderer deterministic and preserves ANSI styling.
"""

from __future__ import annotations

from . import core


TAB_SIZE = 8


def expand_tabs_ansi(text: str, tabsize: int = TAB_SIZE) -> str:
    """Expand tabs to pane-local tab stops while preserving ANSI sequences."""
    if "\t" not in text:
        return text
    tabsize = max(1, int(tabsize))
    out: list[str] = []
    column = 0
    pos = 0
    while pos < len(text):
        match = core.ANSI_RE.match(text, pos)
        if match is not None:
            out.append(match.group(0))
            pos = match.end()
            continue
        char = text[pos]
        if char == "\t":
            spaces = tabsize - (column % tabsize)
            out.append(" " * spaces)
            column += spaces
        else:
            out.append(char)
            column += 1
        pos += 1
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
    Pane._htail_terminal_cells_extension = True
