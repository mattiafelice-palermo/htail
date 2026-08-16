#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "htail_app"
RAPIDFUZZ_VERSION = "3.14.5"
SUPPORTED_CPYTHON_ABIS = ("cp310", "cp311", "cp312", "cp313", "cp314")
WHEEL_PLATFORM = "manylinux_2_28_x86_64"
_FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)


def read_version() -> str:
    text = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("could not resolve VERSION")
    return match.group(1)


def _write_deterministic(archive: zipfile.ZipFile, name: str, data: bytes, executable: bool = False) -> None:
    info = zipfile.ZipInfo(name.replace(os.sep, "/"), _FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = ((0o755 if executable else 0o644) & 0xFFFF) << 16
    archive.writestr(info, data)


def _download_rapidfuzz_wheel(abi: str, wheel_dir: Path) -> Path:
    wheel_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(wheel_dir.glob(f"rapidfuzz-{RAPIDFUZZ_VERSION}-{abi}-{abi}-*.whl"))
    if existing:
        return existing[0]
    pyver = abi.removeprefix("cp")
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--disable-pip-version-check",
        "--no-deps",
        "--only-binary=:all:",
        "--implementation=cp",
        f"--python-version={pyver}",
        f"--abi={abi}",
        f"--platform={WHEEL_PLATFORM}",
        "--dest",
        str(wheel_dir),
        f"RapidFuzz=={RAPIDFUZZ_VERSION}",
    ]
    subprocess.run(command, check=True)
    matches = sorted(wheel_dir.glob(f"rapidfuzz-{RAPIDFUZZ_VERSION}-{abi}-{abi}-*.whl"))
    if not matches:
        raise SystemExit(f"could not download RapidFuzz wheel for {abi}")
    return matches[0]


def build_payload(include_vendor: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        launcher = b"from htail_app.app import main\nraise SystemExit(main())\n"
        _write_deterministic(archive, "launcher.py", launcher)
        for path in sorted(PACKAGE.rglob("*.py")):
            _write_deterministic(archive, str(Path("app") / "htail_app" / path.relative_to(PACKAGE)), path.read_bytes())

        manifest = {
            "format": 2,
            "platform": "linux-x86_64",
            "vendor": {},
        }
        if include_vendor:
            with tempfile.TemporaryDirectory(prefix="htail-wheels-") as td:
                wheel_dir = Path(td)
                for abi in SUPPORTED_CPYTHON_ABIS:
                    wheel = _download_rapidfuzz_wheel(abi, wheel_dir)
                    with zipfile.ZipFile(wheel) as package:
                        for entry in sorted(package.infolist(), key=lambda item: item.filename):
                            if entry.is_dir():
                                continue
                            _write_deterministic(
                                archive,
                                str(Path("vendor") / abi / entry.filename),
                                package.read(entry.filename),
                                executable=bool((entry.external_attr >> 16) & 0o111),
                            )
                    manifest["vendor"][abi] = {
                        "rapidfuzz": RAPIDFUZZ_VERSION,
                        "wheel": wheel.name,
                    }
        _write_deterministic(archive, "bundle.json", json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"))
    return buf.getvalue()


def build_wrapper(version: str, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    encoded = base64.b64encode(payload).decode("ascii")
    chunks = "\n".join(f"    {chunk!r}," for chunk in textwrap.wrap(encoded, 100))
    return f'''#!/usr/bin/env python3
# Generated release bundle. Source checkouts use the small repository launcher instead.
HTAIL_VERSION = "{version}"
_PAYLOAD_SHA256 = "{digest}"
_PAYLOAD = (\n{chunks}\n)

import base64
import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import zipfile


def _extract_environment(payload: bytes) -> Path:
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "htail" / HTAIL_VERSION
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / f"env-{{_PAYLOAD_SHA256[:16]}}"
    if target.is_dir():
        return target
    temp = Path(tempfile.mkdtemp(prefix=".env-", dir=str(cache_root)))
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            archive.extractall(temp)
        try:
            os.replace(temp, target)
        except OSError:
            if not target.is_dir():
                raise
        return target
    finally:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)


def _main():
    if sys.argv[1:] == ["--version"]:
        print(f"htail {{HTAIL_VERSION}}")
        return 0
    payload = base64.b64decode("".join(_PAYLOAD).encode("ascii"))
    if hashlib.sha256(payload).hexdigest() != _PAYLOAD_SHA256:
        raise SystemExit("htail: embedded application payload failed integrity verification")
    env_dir = _extract_environment(payload)
    app_dir = env_dir / "app"
    abi = f"cp{{sys.version_info.major}}{{sys.version_info.minor}}"
    vendor_dir = env_dir / "vendor" / abi
    python_paths = [str(app_dir)]
    if sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64") and vendor_dir.is_dir():
        python_paths.insert(0, str(vendor_dir))
    previous = os.environ.get("PYTHONPATH")
    if previous:
        python_paths.append(previous)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    env["HTAIL_EXECUTABLE"] = str(Path(sys.argv[0]).resolve())
    os.execve(sys.executable, [sys.executable, str(env_dir / "launcher.py"), *sys.argv[1:]], env)


if __name__ == "__main__":
    raise SystemExit(_main())
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "htail")
    parser.add_argument("--no-vendor", action="store_true", help="development-only: omit native dependencies")
    args = parser.parse_args()
    version = read_version()
    wrapper = build_wrapper(version, build_payload(include_vendor=not args.no_vendor))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(wrapper, encoding="utf-8", newline="\n")
    args.output.chmod(0o755)
    print(f"built htail {version}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
