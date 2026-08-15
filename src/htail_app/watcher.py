from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
import time
from typing import List, Optional, Sequence, Tuple

from . import core


@dataclass
class WatchUpdate:
    update_number: int
    events: Sequence[Tuple[str, List[str]]]
    added: int
    replaced: int
    deleted: int
    elapsed: Optional[float]
    now_monotonic: float
    current_snapshot: Sequence[str]
    changed_new_indices: Sequence[int]


@dataclass
class WatchNotice:
    kind: str  # initial, missing, resumed, error
    text: str = ""
    initial_tail: Optional[List[str]] = None


def _changed_new_indices(old: Sequence[str], new: Sequence[str]) -> List[int]:
    """Return current-snapshot row indexes that were added or replaced.

    This mirrors core.compute_changes()'s position-anchored diff semantics so
    repetitive coordination files do not align a fresh tail with older blocks.
    Deletions have no row in the new snapshot and therefore no gutter index.
    """
    old_keys = [core._line_identity(line) for line in old]
    new_keys = [core._line_identity(line) for line in new]

    prefix = 0
    prefix_limit = min(len(old), len(new))
    while prefix < prefix_limit and old_keys[prefix] == new_keys[prefix]:
        prefix += 1

    if prefix == len(old) and len(new) >= len(old):
        return list(range(prefix, len(new)))

    suffix = 0
    suffix_limit = min(len(old) - prefix, len(new) - prefix)
    while (
        suffix < suffix_limit
        and old_keys[len(old) - 1 - suffix] == new_keys[len(new) - 1 - suffix]
    ):
        suffix += 1

    old_end = len(old) - suffix if suffix else len(old)
    new_end = len(new) - suffix if suffix else len(new)
    if suffix == 0:
        return list(range(prefix, new_end))

    matcher = difflib.SequenceMatcher(
        a=old_keys[prefix:old_end],
        b=new_keys[prefix:new_end],
        autojunk=False,
    )
    changed: List[int] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            changed.extend(range(prefix + j1, prefix + j2))
    return changed


class FileFollower:
    """Non-blocking polling state machine for one watched file."""

    def __init__(self, path: Path, args) -> None:
        self.path = path
        self.args = args
        self.previous: List[str] = []
        self.signature = None
        self.last_content_verify = time.monotonic()
        self.active_verify_until = 0.0
        self.last_update_time: Optional[float] = None
        self.update_number = 0
        self.initialized = False
        self.file_missing = False
        self._pending_signature = None
        self._pending_started: Optional[float] = None
        self._pending_last_change: Optional[float] = None

    def _reset_pending(self) -> None:
        self._pending_signature = None
        self._pending_started = None
        self._pending_last_change = None

    def initialize_if_available(self) -> Optional[WatchNotice]:
        if self.initialized:
            return None
        if not self.path.exists():
            return None
        try:
            previous = core.read_lines(self.path, self.args.encoding)
            if self.args.lines is None:
                initial_tail = list(previous)
            elif self.args.lines == 0:
                initial_tail = []
            else:
                initial_tail = list(previous[-self.args.lines:])
        except OSError as exc:
            return WatchNotice("error", f"cannot read {self.path}: {exc}")
        self.previous = previous
        self.signature = core.file_signature(self.path)
        self.last_content_verify = time.monotonic()
        self.initialized = True
        resumed = self.file_missing
        self.file_missing = False
        return WatchNotice("resumed" if resumed else "initial", initial_tail=initial_tail)

    def poll(self, now: Optional[float] = None):
        """Return a WatchUpdate/WatchNotice when state changes, otherwise None."""
        now = time.monotonic() if now is None else now
        if not self.initialized:
            notice = self.initialize_if_available()
            if notice is not None:
                return notice
            if not self.file_missing:
                self.file_missing = True
                return WatchNotice("missing", f"waiting for {self.path}")
            return None

        current_signature = core.file_signature(self.path)
        periodic_verify_due = (
            self.args.verify_interval > 0
            and now - self.last_content_verify >= self.args.verify_interval
        )
        active_verify_due = now <= self.active_verify_until
        verify_due = periodic_verify_due or active_verify_due

        if current_signature is None:
            if not self.file_missing:
                self.file_missing = True
                self.signature = None
                self.last_content_verify = now
                self._reset_pending()
                return WatchNotice("missing", f"{self.path} disappeared; waiting for it to return")
            return None

        was_missing = self.file_missing
        if self.file_missing:
            # Keep the old snapshot so the return can be diffed against the last
            # known content rather than losing changes made while absent.
            self.file_missing = False
            self.signature = None
            self._reset_pending()

        metadata_changed = current_signature != self.signature
        if not metadata_changed and not verify_due:
            return None

        if metadata_changed:
            if self._pending_signature != current_signature:
                if self._pending_started is None:
                    self._pending_started = now
                self._pending_signature = current_signature
                self._pending_last_change = now
                return None

            pending_started = self._pending_started if self._pending_started is not None else now
            pending_last = self._pending_last_change if self._pending_last_change is not None else now
            quiet_for = now - pending_last
            pending_for = now - pending_started
            if quiet_for < self.args.debounce and pending_for < self.args.max_debounce:
                return None
            stable_signature = current_signature
            self._reset_pending()
        else:
            stable_signature = current_signature
            self._reset_pending()

        try:
            current, verified_signature = core.read_verified_snapshot(self.path, self.args.encoding)
        except FileNotFoundError:
            self.signature = None
            self.last_content_verify = now
            return None
        except OSError as exc:
            self.signature = stable_signature
            self.last_content_verify = now
            return WatchNotice("error", f"read error: {exc}")

        if verified_signature is not None:
            stable_signature = verified_signature
        self.last_content_verify = now
        events, added, replaced, deleted = core.compute_changes(self.previous, current)
        changed_new_indices = _changed_new_indices(self.previous, current)
        self.previous = current
        self.signature = stable_signature

        if not events:
            if was_missing:
                return WatchNotice("resumed", f"resumed {self.path}")
            return None

        elapsed = None if self.last_update_time is None else now - self.last_update_time
        self.update_number += 1
        self.last_update_time = now
        self.active_verify_until = max(self.active_verify_until, now + core.ACTIVE_VERIFY_WINDOW)
        return WatchUpdate(
            update_number=self.update_number,
            events=events,
            added=added,
            replaced=replaced,
            deleted=deleted,
            elapsed=elapsed,
            now_monotonic=now,
            current_snapshot=current,
            changed_new_indices=changed_new_indices,
        )
