from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re
import time
from typing import Dict, List, Optional, Pattern, Sequence, Tuple

from . import core
from .searching import SEARCH_REGEX, SEARCH_SIMPLE, compile_search, search_label


FOLLOW_CHANGES = "changes"
FOLLOW_TAIL = "tail"
SELECTED_SEARCH_STYLE = "\x1b[1;30;48;5;208m"


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


def _active_sgr_prefix(text: str, end: int) -> str:
    """Replay the visible SGR state active immediately before ``end``."""
    active: List[str] = []
    for match in core.ANSI_RE.finditer(text[:end]):
        seq = match.group(0)
        if not seq.endswith("m"):
            continue
        if seq in ("\x1b[0m", "\x1b[m"):
            active = []
        else:
            active.append(seq)
    return "".join(active)


def _inject_selected_regex_style(text: str, pattern: Optional[Pattern[str]]) -> str:
    """Render selected search spans as guaranteed black-on-bright-yellow.

    Syntax-highlighting SGR inside a match can otherwise turn the foreground
    white again, which produced low-contrast white/yellow combinations. Strip
    styling only inside the selected span, then restore the surrounding row's
    SGR state after it.
    """
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

    selected_on = SELECTED_SEARCH_STYLE
    for start, end in reversed(spans):
        raw_start = boundaries[start]
        raw_end = boundaries[end]
        restore = _active_sgr_prefix(text, raw_start)
        selected_plain = core.strip_ansi(text[raw_start:raw_end])
        text = (
            text[:raw_start]
            + selected_on
            + selected_plain
            + core.RESET
            + restore
            + text[raw_end:]
        )
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
        # Startup follows EOF until the user actually navigates or the first
        # update arrives. Unlike the legacy one-shot flag, this survives a
        # terminal/layout geometry change between the first two renders.
        self._startup_follow_eof = True
        self.follow_mode = FOLLOW_CHANGES
        self.tail_auto_follow = True
        self._snapshot_tail_pending = False

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
        self.search_flags = 0
        self.search_regex: Optional[Pattern[str]] = None
        self._search_last_target: Optional[int] = None
        self._search_match_position: Optional[int] = None
        self._search_match_total = 0
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

    def _apply_regex_marks(self, row: str, search_index: Optional[int] = None) -> str:
        if not self.color:
            return row
        row = _inject_regex_style(row, self.highlight_regex, "\x1b[4m", "\x1b[24m")
        if self.search_regex is not None:
            if search_index is not None and search_index == self._search_last_target:
                row = _inject_selected_regex_style(row, self.search_regex)
            else:
                row = _inject_regex_style(row, self.search_regex, "\x1b[7m", "\x1b[27m")
        return row

    def set_search(self, expression: str, flags: int = 0, mode: str = SEARCH_REGEX) -> Optional[str]:
        if not expression:
            self.search_pattern = ""
            self.search_mode = mode
            self.search_flags = flags
            self.search_regex = None
            self._search_last_target = None
            self._search_match_position = None
            self._search_match_total = 0
            self._mark_layout_dirty()
            self._snapshot_layout_dirty = True
            return None
        compiled, error = compile_search(expression, mode, flags)
        if error is not None:
            return error
        self.search_pattern = expression
        self.search_mode = mode
        self.search_flags = flags
        self.search_regex = compiled
        self._search_last_target = None
        self._refresh_search_position()
        self._mark_layout_dirty()
        self._snapshot_layout_dirty = True
        return None

    def _search_display(self) -> str:
        return search_label(self.search_pattern, self.search_mode)

    def search_state(self) -> Tuple[str, str, int, Optional[int]]:
        return self.search_pattern, self.search_mode, self.search_flags, self._search_last_target

    def restore_search_state(self, state: Tuple[str, str, int, Optional[int]]) -> None:
        expression, mode, flags, target = state
        error = self.set_search(expression, flags, mode=mode)
        if error is None and target is not None and target in self._search_candidates():
            self._set_search_target(target)

    def search_badge_text(self) -> Optional[str]:
        if self.search_regex is None:
            return None
        if self._search_match_total <= 0:
            return "0 MATCHES"
        position = self._search_match_position or 0
        return f"{position}/{self._search_match_total} MATCHES"

    def _search_candidates(self) -> List[int]:
        pattern = self.search_regex
        if pattern is None:
            return []
        if self.snapshot_raw:
            return [
                i for i, line in enumerate(self.snapshot_raw)
                if self.display_filter.accepts(line)
                and pattern.search(line.rstrip("\r\n")) is not None
            ]
        return [
            i for i, line in enumerate(self.lines)
            if pattern.search(core.strip_ansi(line)) is not None
        ]

    def _refresh_search_position(self) -> List[int]:
        candidates = self._search_candidates()
        self._search_match_total = len(candidates)
        if self._search_last_target in candidates:
            self._search_match_position = candidates.index(self._search_last_target) + 1
        else:
            self._search_match_position = None
        return candidates

    def _set_search_target(self, target: int) -> None:
        self._search_last_target = target
        self._refresh_search_position()
        # Search styling is cached in both layouts; selecting another hit must
        # repaint the old and new selected lines.
        self._mark_layout_dirty()
        self._snapshot_layout_dirty = True

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
        self._startup_follow_eof = False
        if self.follow_mode == FOLLOW_TAIL:
            self.tail_auto_follow = False
        self.prefer_snapshot = True
        self._snapshot_anchor_pending = False
        self._snapshot_tail_pending = False
        self._ensure_snapshot_layout(max(1, width))
        visual = self._snapshot_source_to_visual.get(source_index)
        if visual is None:
            return False
        body_height = max(1, body_height)
        desired = max(0, visual - body_height // 2)
        self._snapshot_top = min(desired, self._snapshot_max_top(body_height))
        self._set_search_target(source_index)
        return True

    def select_search_match(self, ordinal: int, width: int, body_height: int) -> bool:
        """Select one current match by ordinal without emitting a transient message."""
        candidates = self._refresh_search_position()
        if not candidates:
            self._search_last_target = None
            self._search_match_position = None
            self._mark_layout_dirty()
            self._snapshot_layout_dirty = True
            return False
        target = candidates[ordinal % len(candidates)]
        width = max(1, width)
        body_height = max(1, body_height)
        self._startup_follow_eof = False
        if self.follow_mode == FOLLOW_TAIL:
            self.tail_auto_follow = False
        if self.snapshot_raw:
            self.prefer_snapshot = True
            self._snapshot_anchor_pending = False
            self._snapshot_tail_pending = False
            self._ensure_snapshot_layout(width)
            self._snapshot_top = min(
                self._snapshot_source_to_visual[target],
                self._snapshot_max_top(body_height),
            )
        else:
            self._ensure_layout(width)
            self.top = min(self._logical_to_visual[target], self._max_top(body_height))
        self._set_search_target(target)
        return True

    def search_next(self, reverse: bool, width: int, body_height: int) -> bool:
        pattern = self.search_regex
        if pattern is None:
            self.set_message("no active search")
            return False

        self._startup_follow_eof = False
        if self.follow_mode == FOLLOW_TAIL:
            self.tail_auto_follow = False
        width = max(1, width)
        body_height = max(1, body_height)
        candidates = self._refresh_search_position()
        if not candidates:
            self._search_last_target = None
            self._search_match_position = None
            self._mark_layout_dirty()
            self._snapshot_layout_dirty = True
            self.set_message(f"no match: {self._search_display()}")
            return False

        if self.snapshot_raw:
            self.prefer_snapshot = True
            self._snapshot_anchor_pending = False
            self._snapshot_tail_pending = False
            self._ensure_snapshot_layout(width)
            current_source = self._search_last_target if self._search_last_target is not None else -1
            if self._search_last_target is None and self._snapshot_visual_to_source:
                start = min(max(0, self._snapshot_top), len(self._snapshot_visual_to_source) - 1)
                for source in self._snapshot_visual_to_source[start:]:
                    if source is not None:
                        current_source = source
                        break
            if reverse:
                prior = [i for i in candidates if i < current_source or (self._search_last_target is None and i == current_source)]
                target = prior[-1] if prior else candidates[-1]
            else:
                later = [i for i in candidates if i > current_source or (self._search_last_target is None and i == current_source)]
                target = later[0] if later else candidates[0]
            self._snapshot_top = min(
                self._snapshot_source_to_visual[target],
                self._snapshot_max_top(body_height),
            )
            self._set_search_target(target)
            self.set_message(f"match {self._search_match_position}/{self._search_match_total}: {self._search_display()}")
            return True

        self._ensure_layout(width)
        current = self._search_last_target if self._search_last_target is not None else self._logical_at_top()
        if reverse:
            prior = [i for i in candidates if i < current or (self._search_last_target is None and i == current)]
            target = prior[-1] if prior else candidates[-1]
        else:
            later = [i for i in candidates if i > current or (self._search_last_target is None and i == current)]
            target = later[0] if later else candidates[0]
        self.top = min(self._logical_to_visual[target], self._max_top(body_height))
        self._set_search_target(target)
        self.set_message(f"match {self._search_match_position}/{self._search_match_total}: {self._search_display()}")
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
            wrapped = self._wrap_cached(self._apply_regex_marks(line, logical_index), width)
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
        self.snapshot_update_header = update_header
        self._snapshot_layout_dirty = True
        if self.search_regex is not None:
            self._refresh_search_position()
        if prefer:
            self._startup_follow_eof = False
            if self.follow_mode == FOLLOW_TAIL:
                if self.tail_auto_follow:
                    self.prefer_snapshot = True
                    self._snapshot_tail_pending = True
                    self._snapshot_anchor_pending = False
                # If the user manually left EOF in TAIL mode, retain whichever
                # view they are inspecting instead of yanking them back.
            else:
                self.prefer_snapshot = True
                self._snapshot_anchor_pending = True
                self._snapshot_tail_pending = False

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
            row = self._apply_regex_marks(row, source_index)
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
        if self._snapshot_tail_pending:
            # Body height is not known here; park at the final row and the
            # viewport clamp in render_box/_snapshot_view_rows will convert it
            # to the last full screenful.
            self._snapshot_top = max(0, len(visual) - 1)
            self._snapshot_tail_pending = False
        elif self._snapshot_anchor_pending:
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
        self._startup_follow_eof = True
        self.waiting = False
        self.missing = False

    def _max_top(self, body_height: int) -> int:
        """Last legal top row that still keeps EOF inside the viewport."""
        return max(0, len(self._visual_lines) - max(0, body_height))

    def _apply_initial_bottom(self, body_height: int) -> None:
        # Keep following EOF across startup geometry/layout changes until the
        # user explicitly navigates or a real update establishes follow-mode
        # semantics. This fixes the old one-shot flag being consumed too early.
        if self._startup_follow_eof or self._initial_bottom_pending:
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

    def toggle_follow_mode(self) -> None:
        self._startup_follow_eof = False
        if self.follow_mode == FOLLOW_CHANGES:
            self.follow_mode = FOLLOW_TAIL
            self.tail_auto_follow = True
            if self.snapshot_raw:
                self.prefer_snapshot = True
                self._snapshot_anchor_pending = False
                self._snapshot_tail_pending = True
                self._snapshot_layout_dirty = True
            else:
                self._initial_bottom_pending = True
            self.set_message("follow mode: TAIL")
        else:
            self.follow_mode = FOLLOW_CHANGES
            self._snapshot_tail_pending = False
            self.set_message("follow mode: CHANGES")

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            self.set_message("paused")
        else:
            self.unseen_updates = 0
            if self.updates and self.snapshot_raw:
                self.prefer_snapshot = True
                if self.follow_mode == FOLLOW_TAIL:
                    self.tail_auto_follow = True
                    self._snapshot_anchor_pending = False
                    self._snapshot_tail_pending = True
                    self._snapshot_layout_dirty = True
                else:
                    self._snapshot_tail_pending = False
                    self._snapshot_anchor_pending = True
            self.set_message("resumed at freshest update")

    def freshest(self) -> None:
        self._startup_follow_eof = False
        if self.snapshot_raw:
            self.unseen_updates = 0
            self.prefer_snapshot = True
            if self.follow_mode == FOLLOW_TAIL:
                self.tail_auto_follow = True
                self._snapshot_anchor_pending = False
                self._snapshot_tail_pending = True
                self._snapshot_layout_dirty = True
            elif self.updates:
                self._snapshot_tail_pending = False
                self._snapshot_anchor_pending = True

    def previous_update(self) -> None:
        if not self.updates:
            return
        self._startup_follow_eof = False
        if self.follow_mode == FOLLOW_TAIL:
            self.tail_auto_follow = False
        self.prefer_snapshot = False
        current = self._logical_at_top()
        candidates = [u for u in self.updates if u.start < current]
        target = candidates[-1] if candidates else self.updates[0]
        self._pending_anchor_logical = target.start

    def next_update(self) -> None:
        if not self.updates:
            return
        self._startup_follow_eof = False
        if self.follow_mode == FOLLOW_TAIL:
            self.tail_auto_follow = False
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
        self._startup_follow_eof = False

        # END is an explicit "return to live tail" action in TAIL mode even if
        # the user is currently looking at historical update records.
        if self.follow_mode == FOLLOW_TAIL and command == "END" and self.snapshot_raw:
            self.tail_auto_follow = True
            self.prefer_snapshot = True
            self._snapshot_anchor_pending = False
            self._snapshot_tail_pending = True
            self._snapshot_layout_dirty = True
            return

        if self.follow_mode == FOLLOW_TAIL and command in ("UP", "PAGEUP", "HOME"):
            self.tail_auto_follow = False

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
            if self.follow_mode == FOLLOW_TAIL and command in ("DOWN", "PAGEDOWN"):
                if self._snapshot_top >= self._snapshot_max_top(body_height):
                    self.tail_auto_follow = True
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
        parts = [f"{index + 1}:{self.name}", state, self.follow_mode.upper()]
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
        title = core.clip_ansi(title, max(1, width - 4))
        visible = len(core.strip_ansi(title))
        remaining = max(1, width - 3 - visible)
        top_plain = "╭─" + core.strip_ansi(title) + "─" * remaining + "╮"
        if self.color:
            border_style = core.BOLD_LIGHT_CYAN if focused else core.DIM
            top = core.paint("╭─", border_style, True) + title + core.paint("─" * remaining + "╮", border_style, True)
            side = core.paint("│", border_style, True)
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
