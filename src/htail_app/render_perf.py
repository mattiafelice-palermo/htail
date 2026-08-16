"""Incremental render fast paths for interactive scrolling.

The compatibility renderer remains the source of truth.  This module layers
small caches and scoped invalidation around it so a one-pane scroll does not
re-render every other pane and repeated viewport decoration is reused.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import pane as pane_module


_VIEWPORT_CACHE_LIMIT = 4096


@dataclass(frozen=True)
class _PaneBoxEntry:
    geometry: Tuple[int, int, bool, int, bool, bool]
    rows: Tuple[str, ...]


_ORIGINAL_VIEWPORT_ROW = pane_module.Pane._viewport_row
_ORIGINAL_PANE_INIT = pane_module.Pane.__init__


def _cache_put(cache: OrderedDict, key, value, limit: int) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)


def _pane_init_with_render_cache(self, *args, **kwargs) -> None:
    _ORIGINAL_PANE_INIT(self, *args, **kwargs)
    self._viewport_row_cache = OrderedDict()
    self.viewport_cache_hits = 0
    self.viewport_cache_misses = 0


def _viewport_row_cached(self, row: str, width: int) -> str:
    cache = getattr(self, "_viewport_row_cache", None)
    if cache is None:
        cache = OrderedDict()
        self._viewport_row_cache = cache
    key = (
        row,
        max(1, width),
        int(getattr(self, "horizontal_offset", 0)),
        bool(getattr(self, "wrap_enabled", True)),
        bool(getattr(self, "color", False)),
    )
    cached = cache.get(key)
    if cached is not None:
        cache.move_to_end(key)
        self.viewport_cache_hits = int(getattr(self, "viewport_cache_hits", 0)) + 1
        return cached
    rendered = _ORIGINAL_VIEWPORT_ROW(self, row, width)
    self.viewport_cache_misses = int(getattr(self, "viewport_cache_misses", 0)) + 1
    _cache_put(cache, key, rendered, _VIEWPORT_CACHE_LIMIT)
    return rendered


def _dirty_get(self) -> bool:
    return bool(getattr(self, "_render_dirty", False))


def _dirty_set(self, value: bool) -> None:
    value = bool(value)
    self._render_dirty = value
    if not value:
        # The just-rendered frame is now the baseline.  Future scoped events
        # can name only the panes that need rebuilding.
        self._render_dirty_panes = set()
        return

    scope = getattr(self, "_render_dirty_assignment_scope", None)
    if scope is None:
        # Existing ``self.dirty = True`` assignments retain their conservative
        # all-pane semantics.  Only explicitly scoped events take the fast path.
        self._render_dirty_panes = None
        return

    current = getattr(self, "_render_dirty_panes", set())
    if current is not None:
        current.update(scope)
        self._render_dirty_panes = current


def _mark_panes_dirty(self, panes: Iterable[int]) -> None:
    pane_set = set(panes)
    self._render_dirty_assignment_scope = pane_set
    try:
        self.dirty = True
    finally:
        self._render_dirty_assignment_scope = None


def _mark_pane_dirty(self, pane_index: Optional[int] = None) -> None:
    if pane_index is None:
        pane_index = -1 if getattr(self, "layout", None) == "stream" else int(getattr(self, "focus", 0))
    _mark_panes_dirty(self, (pane_index,))


def _viewer_scroll_scope(app, event) -> Optional[Set[int]]:
    # Modal/search input has different semantics for the same keys; keep those
    # paths globally dirty and let their existing code remain authoritative.
    if any(
        bool(getattr(app, name, False))
        for name in (
            "palette_active",
            "global_search_active",
            "prompt_mode",
            "update_confirm_active",
            "layout_menu",
            "help_active",
        )
    ):
        return None
    if not isinstance(event, str):
        return None
    if event not in {"UP", "DOWN", "PAGEUP", "PAGEDOWN", "HOME", "END", "LEFT", "RIGHT"}:
        return None
    return {-1 if getattr(app, "layout", None) == "stream" else int(getattr(app, "focus", 0))}


def _install_app_fast_paths() -> None:
    from . import app as app_module

    MultiApp = app_module.MultiApp
    if getattr(MultiApp, "_htail_render_perf_extension", False):
        return

    original_init = MultiApp.__init__
    original_handle_input = MultiApp.handle_input
    original_handle_mouse = MultiApp.handle_mouse

    # Intercept existing dirty assignments.  The legacy/global path remains
    # exactly conservative; scoped wrappers below opt into per-pane invalidation.
    MultiApp.dirty = property(_dirty_get, _dirty_set)

    def app_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        self._render_pane_box_cache: Dict[int, _PaneBoxEntry] = {}
        self._render_dirty_panes = None
        self._render_dirty_assignment_scope = None
        self.render_pane_cache_hits = 0
        self.render_pane_cache_misses = 0

    def pane_boxes(self, width: int, height: int) -> List[str]:
        if self.layout == "stream":
            rects = [app_module.Rect(0, 0, width, height)]
            displayed = [(-1, self.stream, rects[0])]
            self.last_rects = [(-1, rects[0])]
        elif self.maximized and self.panes:
            rect = app_module.Rect(0, 0, width, height)
            displayed = [(self.focus, self.panes[self.focus], rect)]
            self.last_rects = [(self.focus, rect)]
        else:
            weights = None
            if self.layout in ("rows", "columns") and hasattr(self, "_layout_weights"):
                weights = self._layout_weights(self.layout, len(self.panes))
            if weights is None:
                rects = app_module.pane_rects(self.layout, len(self.panes), width, height)
            else:
                rects = app_module.pane_rects(self.layout, len(self.panes), width, height, weights)
            displayed = [
                (i, self.panes[i], rects[i])
                for i in range(min(len(self.panes), len(rects)))
                if rects[i].width > 0 and rects[i].height > 0
            ]
            self.last_rects = [(i, rect) for i, _, rect in displayed]

        dirty_scope = getattr(self, "_render_dirty_panes", None)
        cache: Dict[int, _PaneBoxEntry] = getattr(self, "_render_pane_box_cache", {})
        segments_by_row: List[List[Tuple[int, str, int]]] = [[] for _ in range(height)]

        for index, pane, rect in displayed:
            focused = index == self.focus if index >= 0 else True
            box_index = index if index >= 0 else 0
            inline_search = self.prompt_mode == "search" and focused and rect.height >= 4
            match_status = pane.search_regex is not None and rect.height >= (5 if inline_search else 4)
            reserve = (1 if inline_search else 0) + (1 if match_status else 0)
            render_height = rect.height - reserve
            geometry = (rect.width, render_height, focused, box_index, inline_search, match_status)

            cached = cache.get(index)
            can_reuse = dirty_scope is not None and index not in dirty_scope and cached is not None and cached.geometry == geometry
            if can_reuse:
                box = list(cached.rows)
                self.render_pane_cache_hits += 1
            else:
                box = pane.render_box(rect.width, render_height, focused, box_index)
                if match_status:
                    box.insert(1, self._match_status_row(rect.width, pane, focused))
                if inline_search:
                    box.insert(max(1, len(box) - 1), self._inline_search_row(rect.width, pane))
                cache[index] = _PaneBoxEntry(geometry, tuple(box))
                self.render_pane_cache_misses += 1

            for local_y, row in enumerate(box):
                y = rect.y + local_y
                if 0 <= y < height:
                    segments_by_row[y].append((rect.x, app_module._pad(row, rect.width), rect.width))

        self._render_pane_box_cache = cache
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

    def handle_input(self, event):
        scope = _viewer_scroll_scope(self, event)
        if scope is None:
            return original_handle_input(self, event)
        self._render_dirty_assignment_scope = scope
        try:
            return original_handle_input(self, event)
        finally:
            self._render_dirty_assignment_scope = None

    def handle_mouse(self, event) -> None:
        if getattr(event, "button", None) not in ("wheel_up", "wheel_down"):
            return original_handle_mouse(self, event)
        target = self._pane_at(event.x, event.y)
        if target is None:
            return original_handle_mouse(self, event)
        self._render_dirty_assignment_scope = {target}
        try:
            return original_handle_mouse(self, event)
        finally:
            self._render_dirty_assignment_scope = None

    MultiApp.__init__ = app_init
    MultiApp._pane_boxes = pane_boxes
    MultiApp.handle_input = handle_input
    MultiApp.handle_mouse = handle_mouse
    MultiApp._mark_pane_dirty = _mark_pane_dirty
    MultiApp._mark_panes_dirty = _mark_panes_dirty
    MultiApp._htail_render_perf_extension = True


def install() -> None:
    if getattr(pane_module.Pane, "_htail_render_perf_extension", False):
        return
    pane_module.Pane.__init__ = _pane_init_with_render_cache
    pane_module.Pane._viewport_row = _viewport_row_cached
    pane_module.Pane._htail_render_perf_extension = True
    _install_app_fast_paths()
