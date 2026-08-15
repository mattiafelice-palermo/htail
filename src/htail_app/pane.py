from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import List, Optional, Sequence, Tuple

from . import core


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


class Pane:
    """Per-file display state, independent of terminal geometry."""

    def __init__(
        self,
        path: Path,
        highlighter: core.SyntaxHighlighter,
        display_filter: core.DisplayFilter,
        color: bool,
        idle_warn: float,
    ) -> None:
        self.path = path
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

        # Independently retain the current verified file snapshot. History is
        # still kept in self.lines; this snapshot is used only when the whole
        # current file fits in the pane after an update.
        self.snapshot_raw: List[str] = []
        self.snapshot_changed: set[int] = set()
        self.prefer_snapshot = False

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)

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
            wrapped = core.wrap_ansi(line, width) or [""]
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
    ) -> None:
        self.snapshot_raw = list(raw_lines)
        self.snapshot_changed = set(changed_indices)
        if prefer:
            self.prefer_snapshot = True

    def _snapshot_view_rows(self, width: int, height: int) -> Optional[List[str]]:
        if not self.prefer_snapshot or not self.snapshot_raw or height <= 0:
            return None

        indexed = [
            (index, line)
            for index, line in enumerate(self.snapshot_raw)
            if self.display_filter.accepts(line)
        ]
        if not indexed or len(indexed) > height:
            return None

        raw_visible = [line for _, line in indexed]
        if self.highlighter.enabled:
            styled = self.highlighter.render_lines(raw_visible)
        else:
            styled = [line.rstrip("\r\n") for line in raw_visible]

        visual: List[str] = []
        for (source_index, _), row in zip(indexed, styled):
            if source_index in self.snapshot_changed:
                row = core.paint("▌ ", core.BOLD_LIGHT_CYAN, self.color) + row
            wrapped = core.wrap_ansi(row, width) or [""]
            visual.extend(wrapped)
            if len(visual) > height:
                return None

        return [_pad_ansi(row, width) for row in visual] + [" " * width] * max(0, height - len(visual))

    def add_initial(self, raw_lines: Sequence[str]) -> None:
        visible = [line for line in raw_lines if self.display_filter.accepts(line)]
        self.lines.extend(core.render_initial_lines(visible, self.highlighter))
        self._mark_layout_dirty()
        self.waiting = False
        self.missing = False

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
            self.prefer_snapshot = False
            self.set_message("paused")
        else:
            self.unseen_updates = 0
            if self.updates:
                self._pending_anchor_logical = self.updates[-1].start
                self.prefer_snapshot = True
            self.set_message("resumed at freshest update")

    def freshest(self) -> None:
        if self.updates:
            self._pending_anchor_logical = self.updates[-1].start
            self.unseen_updates = 0
            self.prefer_snapshot = True

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
        if not self._visual_lines:
            return
        # Manual scrolling means the user wants the retained history rather
        # than the automatic short-file snapshot.
        self.prefer_snapshot = False
        page = max(1, body_height - 2)
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
            self.top = max(0, len(self._visual_lines) - body_height)
        self.top = min(max(0, self.top), max(0, len(self._visual_lines) - 1))

    def view_rows(self, width: int, height: int) -> List[str]:
        width = max(1, width)
        height = max(0, height)
        self._ensure_layout(width)
        self.top = min(max(0, self.top), max(0, len(self._visual_lines) - 1))
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

    def title(self, index: int, width: int, focused: bool) -> str:
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
        # Resolve pending update anchors before deriving the title/current update.
        self._ensure_layout(inner)
        title = self.title(index, max(1, width - 4), focused)
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
            bottom = core.paint("╰" + "─" * (width - 2) + "╯", core.BOLD_LIGHT_CYAN if focused else core.DIM, True)
        else:
            top = top_plain
            side = "│"
            bottom = "╰" + "─" * (width - 2) + "╯"

        snapshot_body = self._snapshot_view_rows(inner, body_h)
        body = snapshot_body if snapshot_body is not None else self.view_rows(inner, body_h)
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
