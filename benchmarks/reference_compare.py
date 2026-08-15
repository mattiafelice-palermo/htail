#!/usr/bin/env python3
"""Compare a candidate tree with a published htail reference tag.

The same deterministic probe is executed against both source trees. Behavior
must match exactly; performance is reported as same-machine ratios so absolute
VM/workstation speed does not matter.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


def run_probe(probe: Path, source_root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(probe), "--source-root", str(source_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def extract_tag(repo: Path, tag: str, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", tag],
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as handle:
        handle.extractall(destination)


def ratio(old: float, new: float) -> str:
    if new <= 0:
        return "∞"
    return f"{old / new:.2f}×"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", default="v0.9.0")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    repo = args.repo.resolve()
    probe = repo / "benchmarks" / "reference_probe.py"

    with tempfile.TemporaryDirectory() as td:
        reference_root = Path(td) / "reference"
        reference_root.mkdir()
        extract_tag(repo, args.reference, reference_root)
        reference = run_probe(probe, reference_root)
        candidate = run_probe(probe, repo)

    if candidate["behavior"] != reference["behavior"]:
        print("Behavior mismatch against", args.reference, file=sys.stderr)
        print(json.dumps({"reference": reference["behavior"], "candidate": candidate["behavior"]}, indent=2), file=sys.stderr)
        return 1

    old = reference["performance"]
    new = candidate["performance"]
    print(f"Behavior: exact match with {args.reference}")
    print()
    print("| Metric | reference | candidate | ratio |")
    print("|---|---:|---:|---:|")
    print(
        f"| idle 1000 polls | {old['idle_1000_polls_ms']:.3f} ms | "
        f"{new['idle_1000_polls_ms']:.3f} ms | {ratio(old['idle_1000_polls_ms'], new['idle_1000_polls_ms'])} |"
    )
    print(
        f"| idle stat calls | {old['idle_1000_stat_calls']} | "
        f"{new['idle_1000_stat_calls']} | "
        + ("eliminated" if new['idle_1000_stat_calls'] == 0 else f"{old['idle_1000_stat_calls']/max(1,new['idle_1000_stat_calls']):.2f}× fewer")
        + " |"
    )
    print(
        f"| one status-only redraw | {old['status_redraw_bytes']} bytes | "
        f"{new['status_redraw_bytes']} bytes | {ratio(float(old['status_redraw_bytes']), float(new['status_redraw_bytes']))} less terminal output |"
    )
    print(
        f"| status redraw CPU | {old['status_redraw_ms']:.3f} ms | "
        f"{new['status_redraw_ms']:.3f} ms | {ratio(old['status_redraw_ms'], new['status_redraw_ms'])} |"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
