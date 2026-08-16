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
import shutil
import sys
import tempfile
import textwrap
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "htail_app"
REQUIREMENTS = ROOT / "tools" / "bundle-requirements.txt"
SUPPORTED_ABIS = ("cp310", "cp311", "cp312", "cp313", "cp314")
_FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)


def read_version() -> str:
    text = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("could not resolve VERSION")
    return match.group(1)


def runtime_id() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def _write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name.replace(os.sep, "/"), _FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 & 0xFFFF) << 16
    archive.writestr(info, data)


def build_payload() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        _write(archive, "launcher.py", b"from htail_app.app import main\nraise SystemExit(main())\n")
        for path in sorted(PACKAGE.rglob("*.py")):
            _write(archive, str(Path("app") / "htail_app" / path.relative_to(PACKAGE)), path.read_bytes())
        _write(
            archive,
            "bundle.json",
            json.dumps(
                {
                    "format": 3,
                    "platform": "linux-x86_64",
                    "runtime_id": runtime_id(),
                    "supported_cpython_abis": list(SUPPORTED_ABIS),
                },
                indent=2,
                sort_keys=True,
            ).encode(),
        )
    return buf.getvalue()


def build_wrapper(version: str, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    rid = runtime_id()
    encoded = base64.b64encode(payload).decode("ascii")
    chunks = "\n".join(f"    {chunk!r}," for chunk in textwrap.wrap(encoded, 100))
    return f'''#!/usr/bin/env python3
HTAIL_VERSION = "{version}"
HTAIL_RUNTIME_ID = "{rid}"
_PAYLOAD_SHA256 = "{digest}"
_PAYLOAD = (\n{chunks}\n)

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request
import zipfile

_REPO = "mattiafelice-palermo/htail"


def _cache_root():
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "htail"


def _extract_app(payload):
    root = _cache_root() / HTAIL_VERSION
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"env-{{_PAYLOAD_SHA256[:16]}}"
    if target.is_dir():
        return target
    temp = Path(tempfile.mkdtemp(prefix=".env-", dir=str(root)))
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


def _runtime_target(abi):
    return _cache_root() / "runtime" / HTAIL_RUNTIME_ID / abi


def _legacy_runtime(abi):
    for candidate in sorted((_cache_root() / "0.16.0").glob(f"env-*/vendor/{{abi}}"), reverse=True):
        if candidate.is_dir() and (candidate / "rapidfuzz").is_dir():
            return candidate
    return None


def _install_runtime_bytes(content, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{{target.name}}-", dir=str(target.parent)))
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as outer:
            manifest = json.loads(outer.read("runtime.json").decode("utf-8"))
            if manifest.get("runtime_id") != HTAIL_RUNTIME_ID:
                raise RuntimeError("runtime id does not match htail core")
            for wheel_name in manifest.get("wheels", []):
                with zipfile.ZipFile(io.BytesIO(outer.read("wheels/" + wheel_name))) as wheel:
                    wheel.extractall(temp)
        try:
            os.replace(temp, target)
        except OSError:
            if not target.is_dir():
                raise
    finally:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)


def _bootstrap_runtime(abi):
    target = _runtime_target(abi)
    if target.is_dir():
        return target
    legacy = _legacy_runtime(abi)
    if legacy is not None:
        return legacy
    # Normal self-updates prepare this before restart. This path is only for
    # fresh manual installs or recovery after the runtime cache was removed.
    base = f"https://github.com/{{_REPO}}/releases/download/v{{HTAIL_VERSION}}/htail-runtime-{{abi}}.zip"
    try:
        print(f"htail: preparing native runtime {{abi}}…", file=sys.stderr)
        with urllib.request.urlopen(base + ".sha256", timeout=10.0) as response:
            checksum = response.read().decode("utf-8", errors="replace")
        expected = next((part.lower() for part in checksum.split() if len(part) == 64), None)
        with urllib.request.urlopen(base, timeout=20.0) as response:
            content = response.read()
        if expected and hashlib.sha256(content).hexdigest() != expected:
            raise RuntimeError("runtime checksum verification failed")
        _install_runtime_bytes(content, target)
        return target
    except Exception as exc:
        print(f"htail: native runtime unavailable: {{exc}}", file=sys.stderr)
        return target


def _main():
    if sys.argv[1:] == ["--version"]:
        print(f"htail {{HTAIL_VERSION}}")
        return 0
    payload = base64.b64decode("".join(_PAYLOAD).encode("ascii"))
    if hashlib.sha256(payload).hexdigest() != _PAYLOAD_SHA256:
        raise SystemExit("htail: embedded application payload failed integrity verification")
    env_dir = _extract_app(payload)
    if sys.argv[1:] == ["--prepare-core"]:
        return 0
    abi = f"cp{{sys.version_info.major}}{{sys.version_info.minor}}"
    runtime = _bootstrap_runtime(abi)
    paths = [str(env_dir / "app")]
    if runtime.is_dir():
        paths.insert(0, str(runtime))
    previous = os.environ.get("PYTHONPATH")
    if previous:
        paths.append(previous)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["HTAIL_EXECUTABLE"] = str(Path(sys.argv[0]).resolve())
    os.execve(sys.executable, [sys.executable, str(env_dir / "launcher.py"), *sys.argv[1:]], env)


if __name__ == "__main__":
    raise SystemExit(_main())
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "htail")
    args = parser.parse_args()
    version = read_version()
    wrapper = build_wrapper(version, build_payload())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(wrapper, encoding="utf-8", newline="\n")
    args.output.chmod(0o755)
    print(f"built htail {version}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
