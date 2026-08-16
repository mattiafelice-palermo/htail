#!/usr/bin/env python3
"""Benchmark interactive scrolling and render-cache effectiveness.

The useful comparison is scoped one-pane invalidation versus forcing the
legacy all-pane rebuild path on the same optimized renderer.  Absolute timing
varies by terminal/CPU; the ratio and cache counters are the stable signals.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import tempfile
import time

from htail_app import app, core
from htail_app import render_perf


def median_ms(samples):
    return statistics.median(samples) * 1000.0 if samples else 0.0


def run_case(application, width: int, height: int, iterations: int, scoped: bool):
    pane = application.active_pane()
    samples = []
    hits_before = application.render_pane_cache_hits
    misses_before = application.render_pane_cache_misses
    viewport_hits_before = sum(p.viewport_cache_hits for p in application.panes)
    viewport_misses_before = sum(p.viewport_cache_misses for p in application.panes)

    for index in range(iterations):
        pane.scroll("UP" if index % 2 == 0 else "DOWN", max(1, height - 2))
        if scoped:
            application._mark_pane_dirty(application.focus)
        else:
            application.dirty = True
        start = time.perf_counter()
        application._pane_boxes(width, height)
        samples.append(time.perf_counter() - start)
        application.dirty = False

    return {
        "median_ms": median_ms(samples),
        "pane_hits": application.render_pane_cache_hits - hits_before,
        "pane_misses": application.render_pane_cache_misses - misses_before,
        "viewport_hits": sum(p.viewport_cache_hits for p in application.panes) - viewport_hits_before,
        "viewport_misses": sum(p.viewport_cache_misses for p in application.panes) - viewport_misses_before,
    }


def viewport_decoration_case(application, width: int, height: int, iterations: int):
    pane = application.active_pane()
    pane._ensure_layout(width)
    rows = pane._visual_lines[pane.top : pane.top + max(1, height)]
    if not rows:
        return 0.0, 0.0
    for row in rows:
        pane._viewport_row(row, width)

    cached_samples = []
    uncached_samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        for row in rows:
            pane._viewport_row(row, width)
        cached_samples.append(time.perf_counter() - start)

        start = time.perf_counter()
        for row in rows:
            render_perf._ORIGINAL_VIEWPORT_ROW(pane, row, width)
        uncached_samples.append(time.perf_counter() - start)
    return median_ms(cached_samples), median_ms(uncached_samples)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panes", type=int, default=4)
    parser.add_argument("--lines", type=int, default=8000)
    parser.add_argument("--iterations", type=int, default=250)
    parser.add_argument("--width", type=int, default=180)
    parser.add_argument("--height", type=int, default=44)
    parser.add_argument("--color", action="store_true", help="exercise ANSI/OSC-8 viewport decoration")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        paths = []
        for pane_index in range(args.panes):
            path = root / f"pane-{pane_index}.log"
            path.write_text(
                "".join(
                    f"2026-08-17 INFO pane={pane_index} row={line} https://example.invalid/{line} payload={'x' * 72}\n"
                    for line in range(args.lines)
                ),
                encoding="utf-8",
            )
            paths.append(path)

        ns = app.parse_args([
            *(str(path) for path in paths),
            "--layout", "columns",
            "--no-native-watch",
            "--no-color",
            "--no-self-install-prompt",
        ])
        application = app.MultiApp(ns, args.color, core.DisplayFilter(), core.UpdateService(""))
        try:
            # Prime wrapping, viewport decoration and pane-box caches.
            application._pane_boxes(args.width, args.height)
            application.dirty = False
            application.active_pane().scroll("END", max(1, args.height - 2))
            application._mark_pane_dirty(application.focus)
            application._pane_boxes(args.width, args.height)
            application.dirty = False

            scoped = run_case(application, args.width, args.height, args.iterations, True)
            forced = run_case(application, args.width, args.height, args.iterations, False)
            cached_viewport, uncached_viewport = viewport_decoration_case(
                application,
                max(1, args.width // max(1, args.panes) - 2),
                max(1, args.height - 2),
                max(10, args.iterations // 5),
            )
        finally:
            application.close_native_watch()

    speedup = forced["median_ms"] / max(scoped["median_ms"], 1e-9)
    print(f"panes={args.panes} lines/pane={args.lines} iterations={args.iterations}")
    print(f"scoped scroll median: {scoped['median_ms']:.3f} ms")
    print(f"forced all-pane median: {forced['median_ms']:.3f} ms")
    print(f"scoped speedup: {speedup:.2f}x")
    print(
        "scoped cache: "
        f"pane hits={scoped['pane_hits']} misses={scoped['pane_misses']} · "
        f"viewport hits={scoped['viewport_hits']} misses={scoped['viewport_misses']}"
    )
    print(
        "forced cache: "
        f"pane hits={forced['pane_hits']} misses={forced['pane_misses']} · "
        f"viewport hits={forced['viewport_hits']} misses={forced['viewport_misses']}"
    )
    print(
        f"viewport decoration/screen: cached={cached_viewport:.3f} ms · "
        f"uncached={uncached_viewport:.3f} ms · "
        f"speedup={uncached_viewport / max(cached_viewport, 1e-9):.2f}x"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
