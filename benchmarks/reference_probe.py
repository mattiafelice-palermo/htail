#!/usr/bin/env python3
"""Probe one htail source tree for behavior and representative performance.

This script intentionally uses only APIs that existed in v0.9.0 so the same
probe can run against the published reference tag and a candidate tree.
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import re
from types import SimpleNamespace


def _args(**overrides):
    base = dict(
        encoding="utf-8",
        lines=None,
        verify_interval=9999.0,
        debounce=0.0,
        max_debounce=0.0,
        idle_warn=0.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _plain(core, rows):
    return [core.strip_ansi(row) for row in rows]



def emulate_terminal(output: str, width: int, height: int):
    """Small emulator for the CSI subset htail emits during redraws."""
    screen = [[" "] * width for _ in range(height)]
    row = col = 0
    i = 0
    while i < len(output):
        if output[i] == "\x1b" and i + 1 < len(output) and output[i + 1] == "[":
            j = i + 2
            while j < len(output) and not ("@" <= output[j] <= "~"):
                j += 1
            if j >= len(output):
                break
            params = output[i + 2 : j]
            command = output[j]
            if command == "H":
                fields = params.split(";") if params else []
                row = max(0, (int(fields[0]) if fields and fields[0].isdigit() else 1) - 1)
                col = max(0, (int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else 1) - 1)
            elif command == "J" and params in ("2", "", "0"):
                screen = [[" "] * width for _ in range(height)]
            elif command == "K":
                if 0 <= row < height:
                    start = min(max(0, col), width)
                    for x in range(start, width):
                        screen[row][x] = " "
            # SGR and private-mode cursor/mouse controls have no visible glyph.
            i = j + 1
            continue
        ch = output[i]
        if ch == "\n":
            row = min(height - 1, row + 1)
            col = 0
        elif ch == "\r":
            col = 0
        else:
            if 0 <= row < height and 0 <= col < width:
                screen[row][col] = ch
            col += 1
        i += 1
    return ["".join(line) for line in screen]

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    ns = parser.parse_args()
    source = ns.source_root.resolve() / "src"
    sys.path.insert(0, str(source))

    from htail_app import core
    from htail_app import app as app_module
    from htail_app.pane import Pane
    from htail_app.watcher import FileFollower, WatchUpdate, analyze_changes

    behavior = {}
    diff_cases = [
        (["a\n"], ["a\n", "b\n"]),
        (["a\n", "b\n", "c\n"], ["a\n", "B\n", "c\n"]),
        (["a\n", "b\n"], ["a\n"]),
        (["a\n", "b\n", "c\n"], ["a\n", "x\n", "y\n", "c\n"]),
    ]
    behavior["diff"] = []
    for old, new in diff_cases:
        result = analyze_changes(old, new)
        behavior["diff"].append({
            "events": [[kind, list(lines)] for kind, lines in result.events],
            "added": result.added,
            "replaced": result.replaced,
            "deleted": result.deleted,
            "changed": list(result.changed_new_indices),
        })

    highlighter = core.SyntaxHighlighter(Path("reference.txt"), "none", False)
    pane = Pane(Path("reference.txt"), highlighter, core.DisplayFilter(), False, 0.0)
    initial = ["alpha\n", "beta beta beta beta beta beta beta\n", "gamma\n", "delta\n"]
    pane.add_initial(initial)
    pane.set_snapshot(initial)
    behavior["initial_box"] = _plain(core, pane.render_box(24, 7, True, 0))
    pane.set_snapshot(
        ["alpha\n", "beta beta beta beta beta beta beta\n", "GAMMA\n", "delta\n", "epsilon\n"],
        [2, 4],
        prefer=True,
        update_header="── update 1 · reference ──",
    )
    behavior["updated_box"] = _plain(core, pane.render_box(24, 7, True, 0))
    pane.scroll("UP", 5)
    behavior["updated_scrolled_box"] = _plain(core, pane.render_box(24, 7, True, 0))

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "watch.txt"
        path.write_text("a\n", encoding="utf-8")
        follower = FileFollower(path, _args())
        follower.initialize_if_available()
        path.write_text("a\nb\n", encoding="utf-8")
        notify = getattr(follower, "notify", None)
        if notify is not None:
            notify()
        base = time.monotonic()
        follower.poll(base)
        update = follower.poll(base + 0.001)
        behavior["append_update"] = None if not isinstance(update, WatchUpdate) else {
            "events": [[kind, list(lines)] for kind, lines in update.events],
            "added": update.added,
            "replaced": update.replaced,
            "deleted": update.deleted,
            "changed": list(update.changed_new_indices),
            "snapshot": list(update.current_snapshot),
        }

    performance = {}
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "idle.txt"
        path.write_text("stable\n", encoding="utf-8")
        args = _args(notification_gated=True)
        follower = FileFollower(path, args)
        follower.initialize_if_available()
        original_signature = core.file_signature
        calls = 0

        def counted_signature(target):
            nonlocal calls
            calls += 1
            return original_signature(target)

        core.file_signature = counted_signature
        try:
            start = time.perf_counter()
            now = time.monotonic()
            for i in range(1000):
                follower.poll(now + i * 0.0001)
            elapsed = time.perf_counter() - start
        finally:
            core.file_signature = original_signature
        performance["idle_1000_polls_ms"] = elapsed * 1000.0
        performance["idle_1000_stat_calls"] = calls

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "frame.txt"
        path.write_text("\n".join(f"line {i}" for i in range(200)) + "\n", encoding="utf-8")
        args = app_module.parse_args([str(path), "--no-color"])
        # Newer candidates can disable native watching here; v0.9.0 simply
        # does not have the option/attribute and ignores this assignment.
        setattr(args, "no_native_watch", True)
        update_service = core.UpdateService("")
        application = app_module.MultiApp(args, False, core.DisplayFilter(), update_service)
        application.dimensions = lambda: (120, 40)

        class Capture(io.StringIO):
            def isatty(self):
                return False

        capture = Capture()
        old_stdout = sys.stdout
        try:
            sys.stdout = capture
            application.render()
            first_end = capture.tell()
            application.set_message("reference-status-change", 10.0)
            start = time.perf_counter()
            application.render()
            elapsed = time.perf_counter() - start
            status_end = capture.tell()
            incremental_output = capture.getvalue()[first_end:status_end].encode("utf-8")
            # Exercise a body-changing redraw too. The optimized terminal
            # command stream may differ, but after applying it to the previous
            # frame the content area must be character-for-character equal to
            # v0.9.0. Footer text is excluded because 0.10.0 intentionally
            # adds new controls/version text there.
            application.active_pane().scroll("UP", 5)
            application.dirty = True
            application.render()
            combined_output = capture.getvalue()
        finally:
            sys.stdout = old_stdout
            close = getattr(application, "close_native_watch", None)
            if close is not None:
                close()
        behavior["final_terminal_body"] = emulate_terminal(combined_output, 120, 40)[:-2]
        performance["status_redraw_ms"] = elapsed * 1000.0
        performance["status_redraw_bytes"] = len(incremental_output)

    print(json.dumps({"behavior": behavior, "performance": performance}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
