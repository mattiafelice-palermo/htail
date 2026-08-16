from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re
import time
from typing import Dict, List, Optional, Pattern, Sequence, Tuple

from . import core
from .searching import SEARCH_REGEX, SEARCH_SIMPLE, compile_search, search_label


@dataclass
class PaneUpdate:
    number: int
    start: int
    end: int


def _pad_ansi(text: str, width: int) -> str:
    text = core.clip_ansi(text, max(0, width))
    visible = len(core.strip_ansi(text))
    if visible < width:
        text += " " * (width - visible)
    return text


def _inject_regex_style(text: str, pattern: Optional[Pattern[str]], on: str, off: str) -> str:
    """Apply an SGR attribute to visible regex spans without destroying syntax ANSI."""
    if pattern is None:
        return text
    plain = core.strip_ansi(text)
    spans = [(m.start(), m.end()) for m in pattern.finditer(plain) if m.end() > m.start()]
    if not spans:
        return text

    boundaries: List[int] = [0] * (len(plain) + 1)
    raw = visible = 0
    while raw < len(text) and visible < len(plain):
        match = core.ANSI_RE.match(text, raw)
        if match:
            raw = match.end()
            continue
        boundaries[visible] = raw
        visible += 1
        raw += 1
    boundaries[visible] = raw

    for start, end in reversed(spans):
        raw_start = boundaries[start]
        raw_end = boundaries[end]
        text = text[:raw_end] + off + text[raw_end:]
        text = text[:raw_start] + on + text[raw_start:]
    return text


class Pane:
    """Per-file display state, independent of terminal geometry."""

    def __init__(
        self,
        path: Path,
        highlighter: core.SyntaxHighlighter,
        display_filter: core.DisplayFilter,
        color: bool,
        idle_warn: float,
        display_name: Optional[str] = None,
    ) -> None:
        self.path = path
        self.display_name = display_name
        self.highlighter = highlighter
        self.display_filter = display_filter
        self.color = color
        self.idle_warn = idle_warn
        self.lines: List[str] = []
        self.updates: List[PaneUpdate] = []
        self.top = 0
        self.paused = False
        self.unseen_updates = 0
        self.last_update_monotonic: Optional[float] = None
        self.watch_started_monotonic = time.monotonic()
        self.waiting = False
        self.missing = False
        self.message: Optional[str] = None
        self.message_until = 0.0

        self._layout_dirty = True
        self._layout_width: Optional[int] = None
        self._visual_lines: List[str] = []
        self._logical_to_visual: List[int] = []
        self._visual_to_logical: List[int] = []
        self._pending_anchor_logical: Optional[int] = None
        self._initial_bottom_pending = False

        # Independently retain the current verified file snapshot. History is
        # still kept in self.lines; this snapshot is used only when the whole
        # current file fits in the pane after an update.
        self.snapshot_raw: List[str] = []
        self.snapshot_changed: set[int] = set()
        self.snapshot_update_header: Optional[str] = None
        self.prefer_snapshot = False
        self._snapshot_layout_dirty = True
        self._snapshot_layout_width: Optional[int] = None
        self._snapshot_visual_lines: List[str] = []
        self._snapshot_source_to_visual: Dict[int, int] = {}
        self._snapshot_visual_to_source: List[Optional[int]] = []
        self._snapshot_top = 0
        self._snapshot_anchor_pending = False

        self.search_pattern = ""
        self.search_mode = SEARCH_SIMPLE
        self.search_regex: Optional[Pattern[str]] = None
        self._search_last_target: Optional[int] = None
        self.highlight_pattern = ""
        self.highlight_regex: Optional[Pattern[str]] = None

        self._wrap_cache: "OrderedDict[Tuple[int, str], Tuple[str, ...]]" = OrderedDict()
        self._render_cache: "OrderedDict[str, str]" = OrderedDict()
        self._cache_limit = 12000

    @property
    def name(self) -> str:
        return self.display_name or self.path.name or str(self.path)

    def _cache_put(self, cache, key, value) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self._cache_limit:
            cache.popitem(last=False)

    def _wrap_cached(self, text: str, width: int) -> List[str]:
        key = (max(1, width), text)
        cached = self._wrap_cache.get(key)
        if cached is not None:
            self._wrap_cache.move_to_end(key)
            return list(cached)
        wrapped = tuple(core.wrap_ansi(text, max(1, width)) or [""])
        self._cache_put(self._wrap_cache, key, wrapped)
        return list(wrapped)

    def _render_snapshot_lines(self, raw_visible: Sequence[str]) -> List[str]:
        if not self.highlighter.enabled:
            return [line.rstrip("\r\n") for line in raw_visible]
        if self.highlighter.mode == "markdown-rendered":
            fence_re = re.compile(r"^\s*(?:```|~~~)")
            if not any(fence_re.match(line.rstrip("\r\n")) for line in raw_visible):
                rendered: List[str] = []
                for raw in raw_visible:
                    body = raw.rstrip("\r\n")
                    cached = self._render_cache.get(body)
                    if cached is None:
                        cached = self.highlighter._render_markdown_line(body)
                        self._cache_put(self._render_cache, body, cached)
                    else:
                        self._render_cache.move_to_end(body)
                    rendered.append(cached)
                return rendered
        return self.highlighter.render_lines(raw_visible)

    def _apply_regex_marks(self, row: str) -> str:
        if not self.color:
            return row
        # Underline is the persistent user highlight; reverse video is the
        # current search expression. Attribute-specific off codes preserve the
        # foreground/bold syntax styles already present in the row.
        row = _inject_regex_style(row, self.highlight_regex, "\x1b[4m", "\x1b[24m")
        row = _inject_regex_style(row, self.search_regex, "\x1b[7m", "\x1b[27m")
        return row

    def set_search(self, expression: str, flags: int = 0, mode: str = SEARCH_REGEX) -> Optional[str]:
        if not expression:
            self.search_pattern = ""
            self.search_mode = mode
            self.search_regex = None
            self._search_last_target = None
            self._mark_layout_dirty()
            self._snapshot_layout_dirty = True
            return None
        compiled, error = compile_search(expression, mode, flags)
        if error is not None:
            return error
        self.search_pattern = expression
        self.search_mode = mode
        self.search_regex = compiled
        self._search_last_target = None
        self._mark_layout_dirty()
        self._snapshot_layout_dirty = True
        return None

    def _search_display(self) -> str:
        return search_label(self.search_pattern, self.search_mode)

    def set_highlight(self, expression: str, flags: int = 0) -> Optional[str]:
        if not expression:
            self.clear_highlight()
            return None
        try:
            compiled = re.compile(expression, flags)
        except re.error as exc:
            return str(exc)
        self.highlight_pattern = expression
        self.highlight_regex = compiled
        self._mark_layout_dirty()
        self._snapshot_layout_dirty = True
        return None

    def clear_highlight(self) -> None:
        self.highlight_pattern = ""
        self.highlight_regex = None
        self._mark_layout_dirty()
        self._snapshot_layout_dirty = True
        self.set_message("regex highlight cleared")

    def jump_to_source_line(self, source_index: int, width: int, body_height: int) -> bool:
        """Show a current-snapshot source line, centered when geometry allows."""
        if not self.snapshot_raw:
            return False
        self.prefer_snapshot = True
        self._snapshot_anchor_pending = False
        self._ensure_snapshot_layout(max(1, width))
        visual = self._snapshot_source_to_visual.get(source_index)
        if visual is None:
            return False
        body_height = max(1, body_height)
        desired = max(0, visual - body_height // 2)
        self._snapshot_top = min(desired, self._snapshot_max_top(body_height))
        self._search_last_target = source_index
        return True

    def search_next(self, reverse: bool, width: int, body_height: int) -> bool:
        pattern = self.search_regex
        if pattern is None:
            self.set_message("no active search")
            return False

        width = max(1, width)
        body_height = max(1, body_height)
        if self.snapshot_raw:
            self.prefer_snapshot = True
            self._ensure_snapshot_layout(width)
            candidates = [
                i for i, line in enumerate(self.snapshot_raw)
                if self.display_filter.accepts(line)
                and pattern.search(line.rstrip("\r\n")) is not None
                and i in self._snapshot_source_to_visual
            ]
            if not candidates:
                self.set_message(f"no match: {self._search_display()}")
                return False
            current_source = self._search_last_target if self._search_last_target is not None else -1
            if self._search_last_target is None and self._snapshot_visual_to_source:
                start = min(max(0, self._snapshot_top), len(self._snapshot_visual_to_source) - 1)
                for source in self._snapshot_visual_to_source[start:]:
                    if source is not None:
                        current_source = source
                        break
            if reverse:
                prior = [i for i in candidates if i < current_source]
                target = prior[-1] if prior else candidates[-1]
            else:
                later = [i for i in candidates if i > current_source]
                target = later[0] if later else candidates[0]
            self._snapshot_top = min(
                self._snapshot_source_to_visual[target],
                self._snapshot_max_top(body_height),
            )
            self._search_last_target = target
            position = candidates.index(target) + 1
            self.set_message(f"match {position}/{len(candidates)}: {self._search_display()}")
            return True

        self._ensure_layout(width)
        candidates = [
            i for i, line in enumerate(self.lines)
            if pattern.search(core.strip_ansi(line)) is not None
        ]
        if not candidates:
            self.set_message(f"no match: {self._search_display()}")
            return False
        current = self._search_last_target if self._search_last_target is not None else self._logical_at_top()
        if reverse:
            prior = [i for i in candidates if i < current]
            target = prior[-1] if prior else candidates[-1]
        else:
            later = [i for i in candidates if i > current]
            target = later[0] if later else candidates[0]
        self.top = min(self._logical_to_visual[target], self._max_top(body_height))
        self._search_last_target = target
        position = candidates.index(target) + 1
        self.set_message(f"match {position}/{len(candidates)}: {self._search_display()}")
        return True

    def set_message(self, text: str, duration: float = 2.5) -> None:
        self.message = text
        self.message_until = time.monotonic() + duration

    def _mark_layout_dirty(self) -> None:
        self._layout_dirty = True

    def _ensure_layout(self, width: int) -> None:
        width = max(1, width)
        if not self._layout_dirty and self._layout_width == width:
            return

        old_logical = 0
        old_offset = 0
        if self._visual_to_logical:
            old_top = min(max(0, self.top), len(self._visual_to_logical) - 1)
            old_logical = self._visual_to_logical[old_top]
            if old_logical < len(self._logical_to_visual):
                old_offset = old_top - self._logical_to_visual[old_logical]

        self._layout_width = width
        self._layout_dirty = False
        self._visual_lines = []
        self._logical_to_visual = []
        self._visual_to_logical = []

        for logical_index, line in enumerate(self.lines):
            self._logical_to_visual.append(len(self._visual_lines))
            wrapped = self._wrap_cached(self._apply_regex_marks(line), width)
            self._visual_lines.extend(wrapped)
            self._visual_to_logical.extend([logical_index] * len(wrapped))

        if self._pending_anchor_logical is not None:
            logical = min(self._pending_anchor_logical, max(0, len(self._logical_to_visual) - 1))
            self.top = self._logical_to_visual[logical] if self._logical_to_visual else 0
            self._pending_anchor_logical = None
        elif self._logical_to_visual:
            logical = min(old_logical, len(self._logical_to_visual) - 1)
            start = self._logical_to_visual[logical]
            end = (
                self._logical_to_visual[logical + 1]
                if logical + 1 < len(self._logical_to_visual)
                else len(self._visual_lines)
            )
            self.top = start + min(old_offset, max(0, end - start - 1))
        else:
            self.top = 0

    def _logical_at_top(self) -> int:
        if not self._visual_to_logical:
            return 0
        return self._visual_to_logical[min(max(0, self.top), len(self._visual_to_logical) - 1)]

    def set_snapshot(
        self,
        raw_lines: Sequence[str],
        changed_indices: Sequence[int] = (),
        *,
        prefer: bool = False,
        update_header: Optional[str] = None,
    ) -> None:
        self.snapshot_raw = list(raw_lines)
        self.snapshot_changed = set(changed_indices)
        self._search_last_target = None
        self.snapshot_update_header = update_header
        self._snapshot_layout_dirty = True
        if prefer:
            self.prefer_snapshot = True
            # A fresh update opens at its marker inside the complete current
            # file. Scrolling then stays inside this snapshot; [ and ] are the
            # explicit controls for entering historical update records.
            self._snapshot_anchor_pending = True

    def _ensure_snapshot_layout(self, width: int) -> None:
        width = max(1, width)
        if not self._snapshot_layout_dirty and self._snapshot_layout_width == width:
            return

        self._snapshot_layout_width = width
        self._snapshot_layout_dirty = False
        self._snapshot_source_to_visual = {}
        self._snapshot_visual_to_source = []
        indexed = [
            (index, line)
            for index, line in enumerate(self.snapshot_raw)
            if self.display_filter.accepts(line)
        ]
        raw_visible = [line for _, line in indexed]
        styled = self._render_snapshot_lines(raw_visible)

        visual: List[str] = []
        anchor: Optional[int] = None
        header_inserted = False
        for (source_index, _), row in zip(indexed, styled):
            changed = source_index in self.snapshot_changed
            if changed and self.snapshot_update_header and not header_inserted:
                anchor = len(visual)
                header_rows = self._wrap_cached(self.snapshot_update_header, width)
                visual.extend(header_rows)
                self._snapshot_visual_to_source.extend([None] * len(header_rows))
                header_inserted = True
            if changed:
                row = core.paint("▌ ", core.BOLD_LIGHT_CYAN, self.color) + row
            row = self._apply_regex_marks(row)
            self._snapshot_source_to_visual[source_index] = len(visual)
            wrapped_rows = self._wrap_cached(row, width)
            visual.extend(wrapped_rows)
            self._snapshot_visual_to_source.extend([source_index] * len(wrapped_rows))

        if self.snapshot_update_header and not header_inserted:
            anchor = len(visual)
            header_rows = self._wrap_cached(self.snapshot_update_header, width)
            visual.extend(header_rows)
            self._snapshot_visual_to_source.extend([None] * len(header_rows))

        self._snapshot_visual_lines = visual
        if self._snapshot_anchor_pending:
            self._snapshot_top = anchor if anchor is not None else max(0, len(visual) - 1)
            self._snapshot_anchor_pending = False
        else:
            self._snapshot_top = min(max(0, self._snapshot_top), max(0, len(visual) - 1))

    def _snapshot_max_top(self, body_height: int) -> int:
        return max(0, len(self._snapshot_visual_lines) - max(0, body_height))

    def _snapshot_view_rows(self, width: int, height: int) -> Optional[List[str]]:
        if not self.prefer_snapshot or not self.snapshot_raw or height <= 0:
            return None
        self._ensure_snapshot_layout(width)
        self._snapshot_top = min(max(0, self._snapshot_top), self._snapshot_max_top(height))
        rows = self._snapshot_visual_lines[self._snapshot_top : self._snapshot_top + height]
        return [_pad_ansi(row, width) for row in rows] + [" " * width] * max(0, height - len(rows))

    def _viewport_counts(self, body_height: int) -> Tuple[int, int]:
        if self.prefer_snapshot and self.snapshot_raw:
            above = min(max(0, self._snapshot_top), len(self._snapshot_visual_lines))
            below = max(0, len(self._snapshot_visual_lines) - (above + max(0, body_height)))
            return above, below
        above = min(max(0, self.top), len(self._visual_lines))
        below = max(0, len(self._visual_lines) - (above + max(0, body_height)))
        return above, below

    def add_initial(self, raw_lines: Sequence[str]) -> None:
        visible = [line for line in raw_lines if self.display_filter.accepts(line)]
        self.lines.extend(core.render_initial_lines(visible, self.highlighter))
        self._mark_layout_dirty()
        self._initial_bottom_pending = True
        self.waiting = False
        self.missing = False

    def _max_top(self, body_height: int) -> int:
        """Last legal top row that still keeps EOF inside the viewport."""
        return max(0, len(self._visual_lines) - max(0, body_height))

    def _apply_initial_bottom(self, body_height: int) -> None:
        if self._initial_bottom_pending:
            self.top = self._max_top(body_height)
            self._initial_bottom_pending = False

    def add_system_line(self, text: str, warning: bool = False) -> None:
        if self.lines:
            self.lines.append("")
        style = core.BOLD_YELLOW if warning else core.DIM
        self.lines.append(core.paint(f"[htail] {text}", style, self.color))
        self._mark_layout_dirty()

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
    ) -> Tuple[str, List[str]]:
        filtered_events, visible_count = self.display_filter.apply_events(events)
        if not show_deletions:
            visible_count -= sum(len(lines) for kind, lines in filtered_events if kind == "delete")
            visible_count = max(0, visible_count)
        total_changed = added + replaced + (deleted if show_deletions else 0)
        header = core.format_update_header(
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
        rendered = core.render_event_lines(
            filtered_events,
            highlighter=self.highlighter,
            color=self.color,
            show_deletions=show_deletions,
            mark_replacements=mark_replacements,
        )
        if not rendered and self.display_filter.active:
            rendered = [core.paint("  (no changed lines matched the active filter)", core.DIM, self.color)]

        if self.lines:
            self.lines.append("")
        start = len(self.lines)
        self.lines.append(header)
        self.lines.extend(rendered)
        self._mark_layout_dirty()
        self.updates.append(PaneUpdate(update_number, start, max(start, len(self.lines) - 1)))
        self.last_update_monotonic = now_monotonic
        self.missing = False
        self.waiting = False
        if self.paused:
            self.unseen_updates += 1
        else:
            self._pending_anchor_logical = start
            self.unseen_updates = 0
        return header, rendered

    def idle_seconds(self, now: Optional[float] = None) -> float:
        now = time.monotonic() if now is None else now
        base = self.last_update_monotonic if self.last_update_monotonic is not None else self.watch_started_monotonic
        return max(0.0, now - base)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            self.set_message("paused")
        else:
            self.unseen_updates = 0
            if self.updates and self.snapshot_raw:
                self.prefer_snapshot = True
                self._snapshot_anchor_pending = True
            self.set_message("resumed at freshest update")

    def freshest(self) -> None:
        if self.updates and self.snapshot_raw:
            self.unseen_updates = 0
            self.prefer_snapshot = True
            self._snapshot_anchor_pending = True

    def previous_update(self) -> None:
        if not self.updates:
            return
        self.prefer_snapshot = False
        current = self._logical_at_top()
        candidates = [u for u in self.updates if u.start < current]
        target = candidates[-1] if candidates else self.updates[0]
        self._pending_anchor_logical = target.start

    def next_update(self) -> None:
        if not self.updates:
            return
        self.prefer_snapshot = False
        current = self._logical_at_top()
        candidates = [u for u in self.updates if u.start > current]
        target = candidates[0] if candidates else self.updates[-1]
        self._pending_anchor_logical = target.start

    def clear_display(self) -> None:
        self.lines.clear()
        self.updates.clear()
        self.top = 0
        self.unseen_updates = 0
        self._pending_anchor_logical = None
        self.prefer_snapshot = False
        self._mark_layout_dirty()
        self.set_message("display cleared; tracking continues")

    def scroll(self, command: str, body_height: int) -> None:
        page = max(1, body_height - 2)
        if self.prefer_snapshot and self.snapshot_raw:
            if not self._snapshot_visual_lines:
                return
            if command == "UP":
                self._snapshot_top -= 1
            elif command == "DOWN":
                self._snapshot_top += 1
            elif command == "PAGEUP":
                self._snapshot_top -= page
            elif command == "PAGEDOWN":
                self._snapshot_top += page
            elif command == "HOME":
                self._snapshot_top = 0
            elif command == "END":
                self._snapshot_top = self._snapshot_max_top(body_height)
            self._snapshot_top = min(max(0, self._snapshot_top), self._snapshot_max_top(body_height))
            return

        if not self._visual_lines:
            return
        if command == "UP":
            self.top -= 1
        elif command == "DOWN":
            self.top += 1
        elif command == "PAGEUP":
            self.top -= page
        elif command == "PAGEDOWN":
            self.top += page
        elif command == "HOME":
            self.top = 0
        elif command == "END":
            self.top = self._max_top(body_height)
        self.top = min(max(0, self.top), self._max_top(body_height))

    def view_rows(self, width: int, height: int) -> List[str]:
        width = max(1, width)
        height = max(0, height)
        self._ensure_layout(width)
        self._apply_initial_bottom(height)
        self.top = min(max(0, self.top), self._max_top(height))
        rows = self._visual_lines[self.top : self.top + height]
        return [_pad_ansi(row, width) for row in rows] + [" " * width] * max(0, height - len(rows))

    def current_update_number(self) -> Optional[int]:
        if self.prefer_snapshot and self.updates:
            return self.updates[-1].number
        current = self._logical_at_top()
        result: Optional[int] = None
        for update in self.updates:
            if update.start <= current:
                result = update.number
            else:
                break
        return result

    def title(self, index: int, width: int, focused: bool, body_height: Optional[int] = None) -> str:
        now = time.monotonic()
        if self.message and now <= self.message_until:
            state = self.message
        elif self.missing:
            state = "MISSING"
        elif self.waiting:
            state = "WAITING"
        else:
            state = "PAUSED" if self.paused else "LIVE"
        parts = [f"{index + 1}:{self.name}", state]
        current = self.current_update_number()
        if current is not None:
            parts.append(f"U{current}")
        if self.unseen_updates:
            parts.append(f"+{self.unseen_updates} NEW")
        if body_height is not None:
            above, below = self._viewport_counts(body_height)
            if above:
                parts.append(f"↑{above}")
            if below:
                parts.append(f"↓{below}")
        idle = self.idle_seconds(now)
        if self.idle_warn > 0 and idle >= self.idle_warn:
            parts.append(f"⚠ {core.format_duration(idle)}")
        label = " · ".join(parts)
        return core.paint(label, core.BOLD_LIGHT_CYAN if focused else core.DIM, self.color)

    def render_box(self, width: int, height: int, focused: bool, index: int) -> List[str]:
        width = max(1, width)
        height = max(1, height)
        if width < 4 or height < 3:
            return [_pad_ansi(self.title(index, width, focused), width)] + [" " * width] * (height - 1)

        inner = width - 2
        body_h = height - 2
        # Initial view follows EOF using the actual wrapped pane height.
        self._ensure_layout(inner)
        self._apply_initial_bottom(body_h)
        if self.prefer_snapshot and self.snapshot_raw:
            self._ensure_snapshot_layout(inner)
            self._snapshot_top = min(max(0, self._snapshot_top), self._snapshot_max_top(body_h))
        title = self.title(index, max(1, width - 4), focused, body_h)
        title_plain = core.strip_ansi(title)
        title = core.clip_ansi(title, max(1, width - 4))
        visible = len(core.strip_ansi(title))
        # Corners + the leading separator consume three cells. The title
        # is already clipped to width-4, guaranteeing at least one trailing
        # dash while keeping the top border exactly `width` cells wide.
        remaining = max(1, width - 3 - visible)
        top_plain = "╭─" + core.strip_ansi(title) + "─" * remaining + "╮"
        if self.color:
            border_style = core.BOLD_LIGHT_CYAN if focused else core.DIM
            top = core.paint("╭─", border_style, True) + title + core.paint("─" * remaining + "╮", border_style, True)
            side = core.paint("│", core.BOLD_LIGHT_CYAN if focused else core.DIM, True)
            border_style = core.BOLD_LIGHT_CYAN if focused else core.DIM
        else:
            top = top_plain
            side = "│"
            border_style = ""

        snapshot_body = self._snapshot_view_rows(inner, body_h)
        body = snapshot_body if snapshot_body is not None else self.view_rows(inner, body_h)
        _, below = self._viewport_counts(body_h)
        if below:
            indicator = f" ↓{below} more "
            fill = max(0, width - 2 - len(indicator))
            if self.color:
                bottom = (
                    core.paint("╰" + "─" * fill, border_style, True)
                    + core.paint(indicator, core.BOLD_LIGHT_CYAN, True)
                    + core.paint("╯", border_style, True)
                )
            else:
                bottom = "╰" + "─" * fill + indicator + "╯"
        else:
            bottom_plain = "╰" + "─" * (width - 2) + "╯"
            bottom = core.paint(bottom_plain, border_style, True) if self.color else bottom_plain

        rows = [_pad_ansi(top, width)]
        rows.extend(_pad_ansi(side + row + side, width) for row in body)
        rows.append(_pad_ansi(bottom, width))
        return rows[:height]


class StreamPane(Pane):
    def __init__(self, color: bool, idle_warn: float) -> None:
        # Highlighter/filter are unused because source panes hand us rendered rows.
        dummy = core.SyntaxHighlighter(Path("stream.txt"), "none", color)
        super().__init__(Path("all files"), dummy, core.DisplayFilter(), color, idle_warn)

    def add_source_update(
        self,
        source_index: int,
        source_name: str,
        header: str,
        rendered: Sequence[str],
        now_monotonic: float,
    ) -> None:
        if self.lines:
            self.lines.append("")
        start = len(self.lines)
        source = core.paint(f"━━ [{source_index + 1}] {source_name} ━━", core.BOLD_LIGHT_CYAN, self.color)
        self.lines.extend([source, header, *rendered])
        self._mark_layout_dirty()
        number = len(self.updates) + 1
        self.updates.append(PaneUpdate(number, start, max(start, len(self.lines) - 1)))
        self.last_update_monotonic = now_monotonic
        if self.paused:
            self.unseen_updates += 1
        else:
            self._pending_anchor_logical = start
            self.unseen_updates = 0
