"""Terminal-output fast paths for scoped pane scrolling.

Columns/Grid use rectangular damage writes. Full-width panes additionally use
DECSTBM + CSI S/T when the old/new rendered bodies prove a pure vertical shift.
All ambiguous cases fall back to the normal renderer.
"""

from __future__ import annotations

import sys
from typing import Sequence

from . import render_perf


def _view_top(pane) -> int:
    if bool(getattr(pane, "prefer_snapshot", False)) and bool(getattr(pane, "snapshot_raw", None)):
        return int(getattr(pane, "_snapshot_top", 0))
    return int(getattr(pane, "top", 0))


def _install() -> None:
    from . import app as app_module

    MultiApp = app_module.MultiApp
    if getattr(MultiApp, "_htail_terminal_fast_extension", False):
        return

    original_init = MultiApp.__init__
    original_handle_input = MultiApp.handle_input
    original_handle_mouse = MultiApp.handle_mouse
    original_render = MultiApp.render

    def app_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        self.terminal_rect_fast_paths = 0
        self.terminal_scroll_region_uses = 0
        self.terminal_fast_rows_written = 0
        self.terminal_fast_bytes_written = 0
        self._terminal_scroll_hint = None
        self._terminal_fast_geometry = None
        self._terminal_fast_ready = False

    def rendered_box_entry(self, index: int, pane, rect):
        focused = index == self.focus if index >= 0 else True
        box_index = index if index >= 0 else 0
        inline_search = self.prompt_mode == "search" and focused and rect.height >= 4
        match_status = pane.search_regex is not None and rect.height >= (5 if inline_search else 4)
        reserve = (1 if inline_search else 0) + (1 if match_status else 0)
        geometry = (rect.width, rect.height - reserve, focused, box_index, inline_search, match_status)
        box = pane.render_box(rect.width, rect.height - reserve, focused, box_index)
        if match_status:
            box.insert(1, self._match_status_row(rect.width, pane, focused))
        if inline_search:
            box.insert(max(1, len(box) - 1), self._inline_search_row(rect.width, pane))
        return render_perf._PaneBoxEntry(geometry, tuple(box))

    def handle_input(self, event):
        scope = render_perf._viewer_scroll_scope(self, event)
        if scope is None:
            self._terminal_scroll_hint = None
            return original_handle_input(self, event)
        index = next(iter(scope))
        pane = self.stream if index == -1 else self.panes[index]
        before = _view_top(pane)
        result = original_handle_input(self, event)
        after = _view_top(pane)
        if event in {"UP", "DOWN", "PAGEUP", "PAGEDOWN", "HOME", "END"} and after != before:
            self._terminal_scroll_hint = (index, after - before)
        else:
            self._terminal_scroll_hint = None
        return result

    def handle_mouse(self, event) -> None:
        if getattr(event, "button", None) not in ("wheel_up", "wheel_down"):
            self._terminal_scroll_hint = None
            return original_handle_mouse(self, event)
        target = self._pane_at(event.x, event.y)
        if target is None:
            self._terminal_scroll_hint = None
            return original_handle_mouse(self, event)
        pane = self.stream if target == -1 else self.panes[target]
        before = _view_top(pane)
        result = original_handle_mouse(self, event)
        after = _view_top(pane)
        self._terminal_scroll_hint = (target, after - before) if after != before else None
        return result

    def terminal_geometry(self):
        width, body_height, footer_height = self.content_dimensions()
        rects = tuple((index, rect.x, rect.y, rect.width, rect.height) for index, rect in self.last_rects)
        return (width, body_height, footer_height, self.layout, bool(self.maximized), rects)

    def terminal_write(self, text: str) -> None:
        sys.stdout.write(text)
        self.terminal_fast_bytes_written += len(text.encode("utf-8", errors="ignore"))

    def terminal_write_row(self, rect, local_y: int, row: str) -> None:
        row = app_module._pad(row, rect.width)
        terminal_write(
            self,
            f"\033[{rect.y + local_y + 1};{rect.x + 1}H" + app_module.core.RESET + row + app_module.core.RESET,
        )
        self.terminal_fast_rows_written += 1

    def scroll_equivalent(old_rows: Sequence[str], new_rows: Sequence[str], delta: int) -> bool:
        if len(old_rows) != len(new_rows) or len(old_rows) < 3 or delta == 0:
            return False
        old_body, new_body = old_rows[1:-1], new_rows[1:-1]
        amount = abs(delta)
        if amount <= 0 or amount >= len(old_body):
            return False
        if delta > 0:
            return tuple(old_body[amount:]) == tuple(new_body[:-amount])
        return tuple(old_body[:-amount]) == tuple(new_body[amount:])

    def write_rect_diff(self, rect, old_rows: Sequence[str], new_rows: Sequence[str]) -> None:
        for local_y, (old, new) in enumerate(zip(old_rows, new_rows)):
            if old != new:
                terminal_write_row(self, rect, local_y, new)
        self.terminal_rect_fast_paths += 1

    def write_scroll_region(self, rect, old_rows: Sequence[str], new_rows: Sequence[str], delta: int) -> None:
        amount = abs(delta)
        body_len = len(new_rows) - 2
        top_margin = rect.y + 2
        bottom_margin = rect.y + rect.height - 1
        direction = "S" if delta > 0 else "T"
        terminal_write(self, f"\033[{top_margin};{bottom_margin}r\033[{amount}{direction}\033[r")
        if old_rows[0] != new_rows[0]:
            terminal_write_row(self, rect, 0, new_rows[0])
        if old_rows[-1] != new_rows[-1]:
            terminal_write_row(self, rect, rect.height - 1, new_rows[-1])
        exposed = range(body_len - amount, body_len) if delta > 0 else range(0, amount)
        for body_index in exposed:
            terminal_write_row(self, rect, 1 + body_index, new_rows[1 + body_index])
        self.terminal_scroll_region_uses += 1

    def fast_render(self) -> bool:
        scope = getattr(self, "_render_dirty_panes", None)
        if not self.dirty or scope is None or len(scope) != 1 or not self._terminal_fast_ready:
            return False
        if terminal_geometry(self) != self._terminal_fast_geometry:
            return False
        index = next(iter(scope))
        rect = next((rect for pane_index, rect in self.last_rects if pane_index == index), None)
        if rect is None or rect.width <= 0 or rect.height <= 0:
            return False
        old_entry = self._render_pane_box_cache.get(index)
        if old_entry is None:
            return False
        pane = self.stream if index == -1 else self.panes[index]
        new_entry = rendered_box_entry(self, index, pane, rect)
        if new_entry.geometry != old_entry.geometry or len(new_entry.rows) != len(old_entry.rows):
            return False

        self._render_pane_box_cache[index] = new_entry
        hint = self._terminal_scroll_hint
        content_width, _, _ = self.content_dimensions()
        if (
            hint is not None
            and hint[0] == index
            and rect.x == 0
            and rect.width == content_width
            and scroll_equivalent(old_entry.rows, new_entry.rows, int(hint[1]))
        ):
            write_scroll_region(self, rect, old_entry.rows, new_entry.rows, int(hint[1]))
        else:
            write_rect_diff(self, rect, old_entry.rows, new_entry.rows)

        sys.stdout.flush()
        self.render_frames += 1
        self._last_frame = None
        self._last_frame_geometry = None
        self._terminal_scroll_hint = None
        self.dirty = False
        return True

    def render(self) -> None:
        if fast_render(self):
            return
        original_render(self)
        if not self.dirty:
            self._terminal_fast_geometry = terminal_geometry(self)
            self._terminal_fast_ready = True
        self._terminal_scroll_hint = None

    MultiApp.__init__ = app_init
    MultiApp.handle_input = handle_input
    MultiApp.handle_mouse = handle_mouse
    MultiApp.render = render
    MultiApp._htail_terminal_fast_extension = True


def install() -> None:
    _install()
