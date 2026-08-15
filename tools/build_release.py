#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import io
from pathlib import Path
import re
import textwrap
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "htail_app"


def read_version() -> str:
    text = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("could not resolve VERSION")
    return match.group(1)


def build_payload() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("__main__.py", "from htail_app.app import main\nraise SystemExit(main())\n")
        for path in sorted(PACKAGE.rglob("*.py")):
            archive.write(path, Path("htail_app") / path.relative_to(PACKAGE))
    return buf.getvalue()


def build_wrapper(version: str, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    encoded = base64.b64encode(payload).decode("ascii")
    chunks = "\n".join(f"    {chunk!r}," for chunk in textwrap.wrap(encoded, 100))
    return f'''#!/usr/bin/env python3
# Generated from src/htail_app by tools/build_release.py. Do not edit manually.
HTAIL_VERSION = "{version}"
_PAYLOAD_SHA256 = "{digest}"
_PAYLOAD = (\n{chunks}\n)

import base64
import hashlib
import os
from pathlib import Path
import sys
import tempfile


def _main():
    if sys.argv[1:] == ["--version"]:
        print(f"htail {{HTAIL_VERSION}}")
        return 0
    payload = base64.b64decode("".join(_PAYLOAD).encode("ascii"))
    if hashlib.sha256(payload).hexdigest() != _PAYLOAD_SHA256:
        raise SystemExit("htail: embedded application payload failed integrity verification")
    cache_dir = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "htail" / HTAIL_VERSION
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"app-{{_PAYLOAD_SHA256[:16]}}.pyz"
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != _PAYLOAD_SHA256:
        fd, temp_name = tempfile.mkstemp(prefix=".app-", suffix=".pyz", dir=str(cache_dir))
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    env = os.environ.copy()
    env["HTAIL_EXECUTABLE"] = str(Path(sys.argv[0]).resolve())
    os.execve(sys.executable, [sys.executable, str(target), *sys.argv[1:]], env)


if __name__ == "__main__":
    raise SystemExit(_main())
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "htail")
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
