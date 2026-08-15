from __future__ import annotations

from dataclasses import dataclass
import codecs
import difflib
from pathlib import Path
import time
from typing import List, Optional, Sequence, Tuple

from . import core


ACTIVE_CONTENT_VERIFY_INTERVAL = 0.50


@dataclass
class DiffAnalysis:
    events: Sequence[Tuple[str, List[str]]]
    added: int
    replaced: int
    deleted: int
    changed_new_indices: Sequence[int]
    unchanged_prefix: int
    unchanged_suffix: int


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
    kind: str  # initial, missing, resumed, ended, error
    text: str = ""
    initial_tail: Optional[List[str]] = None


def analyze_changes(old: Sequence[str], new: Sequence[str]) -> DiffAnalysis:
    """Compute htail's diff events and current-row change indexes in one pass."""
    old_keys = [core._line_identity(line) for line in old]
    new_keys = [core._line_identity(line) for line in new]

    prefix = 0
    prefix_limit = min(len(old), len(new))
    while prefix < prefix_limit and old_keys[prefix] == new_keys[prefix]:
        prefix += 1

    if prefix == len(old) and len(new) >= len(old):
        appended = list(new[prefix:])
        events = [("add", appended)] if appended else []
        return DiffAnalysis(
            events=events,
            added=len(appended),
            replaced=0,
            deleted=0,
            changed_new_indices=tuple(range(prefix, len(new))),
            unchanged_prefix=prefix,
            unchanged_suffix=0,
        )

    suffix = 0
    suffix_limit = min(len(old) - prefix, len(new) - prefix)
    while (
        suffix < suffix_limit
        and old_keys[len(old) - 1 - suffix] == new_keys[len(new) - 1 - suffix]
    ):
        suffix += 1

    old_end = len(old) - suffix if suffix else len(old)
    new_end = len(new) - suffix if suffix else len(new)
    old_mid = list(old[prefix:old_end])
    new_mid = list(new[prefix:new_end])
    old_mid_keys = old_keys[prefix:old_end]
    new_mid_keys = new_keys[prefix:new_end]

    if suffix == 0:
        events: List[Tuple[str, List[str]]] = []
        deleted = len(old_mid)
        if old_mid:
            events.append(("delete", old_mid))
        if new_mid:
            if old_mid:
                events.append(("replace", new_mid))
                return DiffAnalysis(
                    events, 0, len(new_mid), deleted,
                    tuple(range(prefix, new_end)), prefix, 0,
                )
            events.append(("add", new_mid))
            return DiffAnalysis(
                events, len(new_mid), 0, 0,
                tuple(range(prefix, new_end)), prefix, 0,
            )
        return DiffAnalysis(events, 0, 0, deleted, (), prefix, 0)

    matcher = difflib.SequenceMatcher(a=old_mid_keys, b=new_mid_keys, autojunk=False)
    events: List[Tuple[str, List[str]]] = []
    changed: List[int] = []
    added = replaced = deleted = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            lines = new_mid[j1:j2]
            if lines:
                events.append(("add", lines))
                added += len(lines)
                changed.extend(range(prefix + j1, prefix + j2))
        elif tag == "delete":
            lines = old_mid[i1:i2]
            if lines:
                events.append(("delete", lines))
                deleted += len(lines)
        elif tag == "replace":
            old_lines = old_mid[i1:i2]
            new_lines = new_mid[j1:j2]
            if old_lines:
                events.append(("delete", old_lines))
                deleted += len(old_lines)
            if new_lines:
                events.append(("replace", new_lines))
                replaced += len(new_lines)
                changed.extend(range(prefix + j1, prefix + j2))

    return DiffAnalysis(events, added, replaced, deleted, tuple(changed), prefix, suffix)


def _chunk_decode_safe(encoding: str) -> bool:
    """Whether independently decoding newly appended bytes is safe enough."""
    try:
        name = codecs.lookup(encoding).name.lower().replace('_', '-')
    except LookupError:
        return False
    blocked = ('utf-16', 'utf-32', 'utf-7', 'iso2022', 'hz')
    return not any(name.startswith(prefix) for prefix in blocked)


class FileFollower:
    """Verified follower whose expensive probes can be woken by native events.

    ``notify()`` is only a scheduling hint. All content identity, debounce,
    append-fast-path and periodic verified-snapshot rules remain here, so the
    observable change semantics are identical whether a native backend exists
    or callers fall back to polling.
    """

    finished = False

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
        self._notification_hint = True
        self.notification_gated = bool(getattr(args, 'notification_gated', False))
        self.fast_append_hits = 0
        self.stat_probe_count = 0

    def close(self) -> None:
        return

    def notify(self) -> None:
        """Wake the next metadata probe after an OS filesystem notification."""
        self._notification_hint = True

    @property
    def has_pending_change(self) -> bool:
        return self._pending_started is not None

    def _reset_pending(self) -> None:
        self._pending_signature = None
        self._pending_started = None
        self._pending_last_change = None

    def _signature(self):
        self.stat_probe_count += 1
        return core.file_signature(self.path)

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
        self.signature = self._signature()
        self.last_content_verify = time.monotonic()
        self.initialized = True
        self._notification_hint = False
        resumed = self.file_missing
        self.file_missing = False
        return WatchNotice("resumed" if resumed else "initial", initial_tail=initial_tail)

    def _make_update(self, analysis: DiffAnalysis, now: float) -> Optional[WatchUpdate]:
        if not analysis.events:
            return None
        elapsed = None if self.last_update_time is None else now - self.last_update_time
        self.update_number += 1
        self.last_update_time = now
        self.active_verify_until = max(self.active_verify_until, now + core.ACTIVE_VERIFY_WINDOW)
        return WatchUpdate(
            update_number=self.update_number,
            events=analysis.events,
            added=analysis.added,
            replaced=analysis.replaced,
            deleted=analysis.deleted,
            elapsed=elapsed,
            now_monotonic=now,
            current_snapshot=self.previous,
            changed_new_indices=analysis.changed_new_indices,
        )

    def _try_fast_append(self, current_signature, now: float) -> Tuple[bool, Optional[WatchUpdate]]:
        """Consume a pure same-file append without rereading the old prefix."""
        if self.signature is None or current_signature is None:
            return False, None
        if not _chunk_decode_safe(self.args.encoding):
            return False, None
        _old_mtime, old_size, old_inode = self.signature
        _new_mtime, new_size, new_inode = current_signature
        if old_inode != new_inode or new_size <= old_size:
            return False, None

        try:
            with self.path.open('rb') as handle:
                handle.seek(old_size)
                payload = handle.read()
            after = self._signature()
        except OSError:
            return False, None
        if after != current_signature or not payload:
            return False, None

        try:
            text = payload.decode(self.args.encoding, errors='replace')
        except (LookupError, UnicodeError):
            return False, None
        appended = text.splitlines(keepends=True)
        if not appended:
            return False, None

        old_len = len(self.previous)
        if self.previous and not self.previous[-1].endswith(('\n', '\r')):
            old_last = self.previous[-1]
            combined = old_last + appended[0]
            rest = appended[1:]
            self.previous[-1] = combined
            self.previous.extend(rest)
            if core._line_identity(combined) == core._line_identity(old_last):
                analysis = DiffAnalysis(
                    events=(("add", list(rest)),) if rest else (),
                    added=len(rest),
                    replaced=0,
                    deleted=0,
                    changed_new_indices=tuple(range(old_len, len(self.previous))),
                    unchanged_prefix=old_len,
                    unchanged_suffix=0,
                )
            else:
                replacement = [combined, *rest]
                analysis = DiffAnalysis(
                    events=(("delete", [old_last]), ("replace", replacement)),
                    added=0,
                    replaced=len(replacement),
                    deleted=1,
                    changed_new_indices=tuple(range(old_len - 1, len(self.previous))),
                    unchanged_prefix=max(0, old_len - 1),
                    unchanged_suffix=0,
                )
        else:
            self.previous.extend(appended)
            analysis = DiffAnalysis(
                events=(("add", list(appended)),),
                added=len(appended),
                replaced=0,
                deleted=0,
                changed_new_indices=tuple(range(old_len, len(self.previous))),
                unchanged_prefix=old_len,
                unchanged_suffix=0,
            )

        self.signature = current_signature
        self.last_content_verify = now
        self.fast_append_hits += 1
        return True, self._make_update(analysis, now)

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

        periodic_verify_due = (
            self.args.verify_interval > 0
            and now - self.last_content_verify >= self.args.verify_interval
        )
        active_verify_due = (
            now <= self.active_verify_until
            and now - self.last_content_verify >= ACTIVE_CONTENT_VERIFY_INTERVAL
        )
        verify_due = periodic_verify_due or active_verify_due

        # Native notifications suppress redundant idle metadata probes. A
        # pending debounced change must continue to be sampled until stable,
        # and periodic verification remains the correctness safety net.
        if self.notification_gated and not self._notification_hint and not self.has_pending_change and not verify_due:
            return None
        self._notification_hint = False

        current_signature = self._signature()
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

            handled, fast = self._try_fast_append(stable_signature, now)
            if handled:
                return fast
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
        analysis = analyze_changes(self.previous, current)
        self.previous = current
        self.signature = stable_signature

        update = self._make_update(analysis, now)
        if update is not None:
            return update
        if was_missing:
            return WatchNotice("resumed", f"resumed {self.path}")
        return None
