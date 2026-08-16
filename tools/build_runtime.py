#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "tools" / "bundle-requirements.txt"
SUPPORTED_ABIS = ("cp310", "cp311", "cp312", "cp313", "cp314")
PLATFORM = "manylinux_2_28_x86_64"


def runtime_id() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def download_wheels(abi: str, target: Path):
    pyver = abi.removeprefix("cp")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "--implementation=cp",
            f"--python-version={pyver}",
            f"--abi={abi}",
            f"--platform={PLATFORM}",
            "--dest",
            str(target),
            "--requirement",
            str(REQUIREMENTS),
        ],
        check=True,
    )
    wheels = sorted(target.glob("*.whl"))
    if not wheels:
        raise SystemExit(f"no wheels resolved for {abi}")
    return wheels


def build_one(abi: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"htail-runtime-{abi}.zip"
    with tempfile.TemporaryDirectory(prefix=f"htail-runtime-{abi}-") as td:
        wheels = download_wheels(abi, Path(td))
        manifest = {
            "format": 1,
            "runtime_id": runtime_id(),
            "abi": abi,
            "platform": PLATFORM,
            "wheels": [wheel.name for wheel in wheels],
        }
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(
                "runtime.json",
                json.dumps(manifest, indent=2, sort_keys=True),
                compress_type=zipfile.ZIP_DEFLATED,
            )
            for wheel in wheels:
                # A wheel is already a compressed ZIP. Store it verbatim so
                # release/CI does not spend time decompressing and recompressing.
                archive.writestr(
                    "wheels/" + wheel.name,
                    wheel.read_bytes(),
                    compress_type=zipfile.ZIP_STORED,
                )
    print(f"built {output} ({output.stat().st_size:,} bytes)")
    return output


def self_test(path: Path) -> None:
    with zipfile.ZipFile(path) as outer:
        manifest = json.loads(outer.read("runtime.json"))
        with tempfile.TemporaryDirectory(prefix="htail-runtime-test-") as td:
            target = Path(td)
            for wheel_name in manifest["wheels"]:
                with zipfile.ZipFile(io.BytesIO(outer.read("wheels/" + wheel_name))) as wheel:
                    wheel.extractall(target)
            env = dict(os.environ)
            env["PYTHONPATH"] = str(target)
            subprocess.run(
                [sys.executable, "-c", "import rapidfuzz; print(rapidfuzz.__version__)"],
                check=True,
                env=env,
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--abi", action="append", choices=SUPPORTED_ABIS)
    parser.add_argument("--self-test", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test(args.self_test)
        return 0
    for abi in (tuple(args.abi) if args.abi else SUPPORTED_ABIS):
        build_one(abi, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
