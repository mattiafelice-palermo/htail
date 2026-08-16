"""Opt-in low-overhead real-use performance tracing.

Unset HTAIL_PERF_TRACE means this module installs no runtime wrappers at all.
When enabled, it writes one-second aggregate JSONL samples and exceptional
full-redraw / slow-render events.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Dict


_SLOW_RENDER_NS = 16_666_667
_SAMPLE_SECONDS = 1.0


def _trace_path(spec: str) -> Path:
    spec = spec.strip()
    if spec.lower() in {"1", "auto", "yes", "true"}:
        root = Path.home() / ".cache" / "htail" / "perf"
        return root / f"htail-{os.getpid()}.jsonl"
    return Path(spec).expanduser()


def _counters(app) -> Dict[str, int]:
    names = (
        "render_frames",
        "render_rows_written",
        "render_pane_cache_hits",
        "render_pane_cache_misses",
        "terminal_rect_fast_paths",
        "terminal_scroll_region_uses",
        "terminal_fast_rows_written",
        "terminal_fast_bytes_written",
        "input_arrow_bursts",
        "input_arrow_events_coalesced",
        "input_arrow_accelerated_rows",
    )
    return {name: int(getattr(app, name, 0)) for name in names}


def install() -> None:
    spec = os.environ.get("HTAIL_PERF_TRACE", "").strip()
    if not spec:
        return

    from . import app as app_module

    MultiApp = app_module.MultiApp
    if getattr(MultiApp, "_htail_perf_trace_extension", False):
        return

    path = _trace_path(spec)
    original_init = MultiApp.__init__
    original_render = MultiApp.render
    original_exit = MultiApp.__exit__

    def emit(self, payload: dict) -> None:
        handle = getattr(self, "_perf_trace_handle", None)
        if handle is None:
            return
        payload = dict(payload)
        payload.setdefault("t", round(time.monotonic() - self._perf_trace_started, 6))
        try:
            handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
        except OSError:
            self._perf_trace_handle = None

    def flush_sample(self, now: float, *, force: bool = False) -> None:
        if self._perf_trace_handle is None:
            return
        if not force and now - self._perf_trace_sample_started < _SAMPLE_SECONDS:
            return
        current = _counters(self)
        delta = {key: current[key] - self._perf_trace_last_counters.get(key, 0) for key in current}
        frames = self._perf_trace_sample_frames
        emit(
            self,
            {
                "event": "sample",
                "seconds": round(max(0.0, now - self._perf_trace_sample_started), 6),
                "frames": frames,
                "render_ms_avg": round(self._perf_trace_sample_ns / frames / 1_000_000, 4) if frames else 0.0,
                "render_ms_max": round(self._perf_trace_sample_max_ns / 1_000_000, 4),
                "full_redraws": self._perf_trace_sample_full_redraws,
                **delta,
            },
        )
        self._perf_trace_last_counters = current
        self._perf_trace_sample_started = now
        self._perf_trace_sample_frames = 0
        self._perf_trace_sample_ns = 0
        self._perf_trace_sample_max_ns = 0
        self._perf_trace_sample_full_redraws = 0
        try:
            self._perf_trace_handle.flush()
        except OSError:
            self._perf_trace_handle = None

    def app_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        self._perf_trace_enabled = False
        self._perf_trace_handle = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._perf_trace_handle = path.open("a", encoding="utf-8", buffering=1)
        except OSError:
            return
        now = time.monotonic()
        self._perf_trace_enabled = True
        self._perf_trace_started = now
        self._perf_trace_sample_started = now
        self._perf_trace_sample_frames = 0
        self._perf_trace_sample_ns = 0
        self._perf_trace_sample_max_ns = 0
        self._perf_trace_sample_full_redraws = 0
        self._perf_trace_last_counters = _counters(self)
        self._perf_trace_seen_frame = False
        emit(self, {"event": "start", "pid": os.getpid(), "path": str(path)})

    def render(self) -> None:
        if not getattr(self, "_perf_trace_enabled", False):
            return original_render(self)
        was_dirty = bool(self.dirty)
        baseline_missing = self._last_frame is None
        before_geometry = self._last_frame_geometry
        scope = getattr(self, "_render_dirty_panes", None)
        started_ns = time.perf_counter_ns()
        original_render(self)
        elapsed_ns = time.perf_counter_ns() - started_ns
        if not was_dirty:
            return
        self._perf_trace_sample_frames += 1
        self._perf_trace_sample_ns += elapsed_ns
        self._perf_trace_sample_max_ns = max(self._perf_trace_sample_max_ns, elapsed_ns)

        after_geometry = self._last_frame_geometry
        full_reason = None
        if self._perf_trace_seen_frame and baseline_missing:
            full_reason = "missing-frame-baseline"
        elif self._perf_trace_seen_frame and before_geometry is not None and after_geometry != before_geometry:
            full_reason = "geometry-change"
        if full_reason is not None:
            self._perf_trace_sample_full_redraws += 1
            emit(
                self,
                {
                    "event": "full_redraw",
                    "reason": full_reason,
                    "dirty_scope": None if scope is None else sorted(scope),
                    "before_geometry": before_geometry,
                    "after_geometry": after_geometry,
                },
            )
        if elapsed_ns > _SLOW_RENDER_NS:
            emit(
                self,
                {
                    "event": "slow_render",
                    "render_ms": round(elapsed_ns / 1_000_000, 4),
                    "dirty_scope": None if scope is None else sorted(scope),
                    "baseline_missing": baseline_missing,
                },
            )
        self._perf_trace_seen_frame = True
        flush_sample(self, time.monotonic())

    def app_exit(self, exc_type, exc, tb) -> None:
        if getattr(self, "_perf_trace_enabled", False):
            flush_sample(self, time.monotonic(), force=True)
            emit(self, {"event": "stop", **_counters(self)})
            handle = self._perf_trace_handle
            self._perf_trace_handle = None
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        return original_exit(self, exc_type, exc, tb)

    MultiApp.__init__ = app_init
    MultiApp.render = render
    MultiApp.__exit__ = app_exit
    MultiApp._htail_perf_trace_extension = True
