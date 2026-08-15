#!/usr/bin/env python3
"""Synthetic htail microbenchmarks.

The benchmark intentionally compares old-style full reread/double-diff work
against the optimized primitives on the same machine. Absolute numbers vary by
filesystem/VM; ratios are the useful part.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import tempfile
import time
from types import SimpleNamespace

from htail_app import core
from htail_app.watcher import FileFollower, analyze_changes


def timed(fn, iterations: int):
    values = []
    result = None
    for _ in range(iterations):
        start = time.perf_counter()
        result = fn()
        values.append(time.perf_counter() - start)
    return statistics.median(values), result


def make_file(path: Path, mib: int) -> None:
    target = mib * 1024 * 1024
    line = ("2026-08-16 INFO synthetic benchmark payload " + "x" * 120 + "\n").encode()
    with path.open('wb') as handle:
        written = 0
        while written < target:
            chunk = line[: min(len(line), target - written)]
            handle.write(chunk)
            written += len(chunk)


def legacy_changed_indices(old, new):
    import difflib
    old_keys = [core._line_identity(line) for line in old]
    new_keys = [core._line_identity(line) for line in new]
    prefix = 0
    while prefix < min(len(old), len(new)) and old_keys[prefix] == new_keys[prefix]:
        prefix += 1
    if prefix == len(old) and len(new) >= len(old):
        return list(range(prefix, len(new)))
    suffix = 0
    limit = min(len(old) - prefix, len(new) - prefix)
    while suffix < limit and old_keys[-1 - suffix] == new_keys[-1 - suffix]:
        suffix += 1
    old_end = len(old) - suffix if suffix else len(old)
    new_end = len(new) - suffix if suffix else len(new)
    if suffix == 0:
        return list(range(prefix, new_end))
    matcher = difflib.SequenceMatcher(a=old_keys[prefix:old_end], b=new_keys[prefix:new_end], autojunk=False)
    changed = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ('insert', 'replace'):
            changed.extend(range(prefix + j1, prefix + j2))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--sizes', type=int, nargs='+', default=[1, 10, 50])
    parser.add_argument('--iterations', type=int, default=3)
    args = parser.parse_args()

    print('| MiB | full reread + old double diff | fast append follower | speedup | single diff vs double diff |')
    print('|---:|---:|---:|---:|---:|')
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for mib in args.sizes:
            path = root / f'{mib}m.log'
            make_file(path, mib)

            legacy_times = []
            for _ in range(args.iterations):
                old = core.read_lines(path, 'utf-8')
                with path.open('a', encoding='utf-8') as handle:
                    handle.write('LEGACY benchmark line\n')
                start = time.perf_counter()
                current, _ = core.read_verified_snapshot(path, 'utf-8')
                core.compute_changes(old, current)
                legacy_changed_indices(old, current)
                legacy_times.append(time.perf_counter() - start)
            legacy = statistics.median(legacy_times)

            ns = SimpleNamespace(encoding='utf-8', lines=None, verify_interval=9999.0, debounce=0.0, max_debounce=0.0)
            follower = FileFollower(path, ns)
            follower.initialize_if_available()
            fast_times = []
            for i in range(args.iterations):
                with path.open('a', encoding='utf-8') as handle:
                    handle.write(f'FAST {i}\n')
                start = time.perf_counter()
                follower.poll(time.monotonic())
                update = follower.poll(time.monotonic() + 0.001)
                fast_times.append(time.perf_counter() - start)
                assert update is not None
            fast = statistics.median(fast_times)

            old = core.read_lines(path, 'utf-8')
            new = list(old)
            middle = len(new) // 2
            new[middle:middle+1] = ['changed middle row\n', 'inserted row\n']
            double_t, _ = timed(lambda: (core.compute_changes(old, new), legacy_changed_indices(old, new)), max(1, args.iterations))
            single_t, _ = timed(lambda: analyze_changes(old, new), max(1, args.iterations))

            print(f'| {mib} | {legacy*1000:.2f} ms | {fast*1000:.3f} ms | {legacy/max(fast,1e-9):.1f}× | {double_t/max(single_t,1e-9):.2f}× |')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
