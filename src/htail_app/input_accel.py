"""Coalesce terminal arrow-repeat backlog and accelerate held vertical scrolling."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Optional, Tuple

from . import input as input_module
from . import render_perf


_REPEAT_WINDOW_SECONDS = 0.140
_MAX_COALESCED_EVENTS = 512
_MAX_MOVEMENT_PER_BURST = 12


@dataclass(frozen=True)
class KeyBurst:
    key: str
    count: int


def _coalesce_key_burst(
    first: str,
    poll_next: Callable[[], object],
    *,
    limit: int = _MAX_COALESCED_EVENTS,
) -> Tuple[KeyBurst, Optional[object]]:
    count = 1
    pending = None
    for _ in range(max(0, limit - 1)):
        event = poll_next()
        if event is None:
            break
        if event == first:
            count += 1
            continue
        pending = event
        break
    return KeyBurst(first, count), pending


def _repeat_step(streak: int) -> int:
    if streak <= 2:
        return 1
    if streak <= 6:
        return 2
    if streak <= 12:
        return 3
    return 4


def _burst_movement(previous_streak: int, count: int) -> int:
    movement = 0
    for offset in range(max(1, count)):
        movement += _repeat_step(previous_streak + offset + 1)
        if movement >= _MAX_MOVEMENT_PER_BURST:
            return _MAX_MOVEMENT_PER_BURST
    return movement


def install() -> None:
    InputReader = input_module.InputReader
    if getattr(InputReader, "_htail_input_accel_extension", False):
        return

    original_poll = InputReader.poll

    def poll(self):
        pending = getattr(self, "_htail_pending_input_event", None)
        if pending is not None:
            self._htail_pending_input_event = None
            first = pending
        else:
            first = original_poll(self)
        if first not in {"UP", "DOWN"}:
            return first
        burst, pending = _coalesce_key_burst(first, lambda: original_poll(self))
        if pending is not None:
            self._htail_pending_input_event = pending
        return burst

    InputReader.poll = poll
    InputReader._htail_input_accel_extension = True

    from . import app as app_module

    MultiApp = app_module.MultiApp
    if getattr(MultiApp, "_htail_input_accel_extension", False):
        return
    original_handle_input = MultiApp.handle_input
    original_init = MultiApp.__init__

    def app_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        self._arrow_repeat_key = None
        self._arrow_repeat_last = 0.0
        self._arrow_repeat_streak = 0
        self.input_arrow_bursts = 0
        self.input_arrow_events_coalesced = 0
        self.input_arrow_accelerated_rows = 0

    def handle_input(self, event):
        if not isinstance(event, KeyBurst):
            return original_handle_input(self, event)

        key = event.key
        now = time.monotonic()
        same_streak = key == self._arrow_repeat_key and now - self._arrow_repeat_last <= _REPEAT_WINDOW_SECONDS
        previous_streak = self._arrow_repeat_streak if same_streak else 0
        self._arrow_repeat_key = key
        self._arrow_repeat_last = now
        self._arrow_repeat_streak = previous_streak + event.count
        self.input_arrow_bursts += 1
        self.input_arrow_events_coalesced += max(0, event.count - 1)

        scope = render_perf._viewer_scroll_scope(self, key)
        if scope is None:
            # Preserve modal/search semantics while still draining queued repeats
            # in one event-loop pass. Do not apply viewer acceleration there.
            result = False
            for _ in range(min(event.count, _MAX_MOVEMENT_PER_BURST)):
                result = bool(original_handle_input(self, key)) or result
            return result

        movement = _burst_movement(previous_streak, event.count)
        self.input_arrow_accelerated_rows += max(0, movement - event.count)
        index = next(iter(scope))
        pane = self.stream if index == -1 else self.panes[index]
        before = int(getattr(pane, "_snapshot_top", 0)) if pane.prefer_snapshot and pane.snapshot_raw else int(pane.top)
        result = False
        for _ in range(movement):
            result = bool(original_handle_input(self, key)) or result
        after = int(getattr(pane, "_snapshot_top", 0)) if pane.prefer_snapshot and pane.snapshot_raw else int(pane.top)
        if after != before and hasattr(self, "_terminal_scroll_hint"):
            # terminal_fast sees the whole visual jump, not merely the final
            # replayed key, so scroll-region equivalence remains correct.
            self._terminal_scroll_hint = (index, after - before)
        return result

    MultiApp.__init__ = app_init
    MultiApp.handle_input = handle_input
    MultiApp._htail_input_accel_extension = True
