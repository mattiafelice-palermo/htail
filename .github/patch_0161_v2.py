from pathlib import Path


def replace_once(path, old, new, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path, start, end, new, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    i = text.find(start)
    j = text.find(end, i + len(start))
    if i < 0 or j < 0:
        raise RuntimeError(f"{label}: markers missing")
    p.write_text(text[:i] + new + text[j:], encoding="utf-8")


# Input: a lone POSIX Esc must not block waiting for another key.
replace_once(
    "src/htail_app/input.py",
    '''    def _read_escape_sequence(self, first: bytes) -> str:\n        seq = first.decode("utf-8", errors="ignore")\n        deadline = time.monotonic() + 0.003\n        while time.monotonic() < deadline:\n            byte = self._read_byte()\n            if not byte:\n                time.sleep(0.0002)\n                continue\n            seq += byte.decode("utf-8", errors="ignore")\n            if seq.endswith(("~", "A", "B", "C", "D", "H", "F", "M", "m", "Z")):\n                break\n        return seq\n''',
    '''    def _input_ready(self, timeout: float) -> bool:\n        if self._fd is None:\n            return False\n        try:\n            import select\n            ready, _, _ = select.select([self._fd], [], [], max(0.0, timeout))\n            return bool(ready)\n        except Exception:\n            return False\n\n    def _read_escape_sequence(self, first: bytes) -> str:\n        seq = first.decode("utf-8", errors="ignore")\n        deadline = time.monotonic() + 0.010\n        while True:\n            remaining = deadline - time.monotonic()\n            if remaining <= 0 or not self._input_ready(remaining):\n                break\n            byte = self._read_byte()\n            if not byte:\n                break\n            seq += byte.decode("utf-8", errors="ignore")\n            if seq.endswith(("~", "A", "B", "C", "D", "H", "F", "M", "m", "Z")):\n                break\n        return seq\n''',
    "nonblocking POSIX Esc",
)

APP = "src/htail_app/app.py"
replace_between(
    APP,
    "    def _refresh_global_search_results(self) -> None:\n",
    "    def _cycle_global_search_file_filter(self, backwards: bool = False) -> None:\n",
    '''    def _refresh_global_search_results(self) -> None:\n        signature, corpus = self._global_search_corpus_data()\n        key = (\n            self.global_search_buffer,\n            self.global_search_mode,\n            self.global_search_ignore_case,\n            self.global_search_sort,\n            self.global_search_file_filter,\n            signature,\n        )\n        if key == self._global_search_cache_key:\n            return\n        self._global_search_cache_key = key\n        try:\n            page = search_corpus(\n                corpus,\n                self.global_search_buffer,\n                self.global_search_mode,\n                self._global_search_flags(),\n                file_filter=self.global_search_file_filter,\n                sort_mode=self.global_search_sort,\n                limit=GLOBAL_SEARCH_LIMIT,\n            )\n        except Exception as exc:\n            self.global_search_results = []\n            self.global_search_error = f"{type(exc).__name__}: {exc}"\n            self.global_search_truncated = False\n            self.global_search_selected = 0\n            return\n        self.global_search_results = page.results\n        self.global_search_error = page.error\n        self.global_search_truncated = page.truncated\n        if self.global_search_results:\n            self.global_search_selected = min(max(0, self.global_search_selected), len(self.global_search_results) - 1)\n        else:\n            self.global_search_selected = 0\n\n''',
    "global search backend guard",
)
replace_between(
    APP,
    "    def _global_search_lines(self, width: int, height: int) -> List[str]:\n",
    "    def _select_global_search_result(self) -> bool:\n",
    '''    def _global_search_lines(self, width: int, height: int) -> List[str]:\n        self._refresh_global_search_results()\n        if self.global_search_file_filter is None:\n            file_label = "[All files]"\n        elif 0 <= self.global_search_file_filter < len(self.panes):\n            file_label = f"[{self.panes[self.global_search_file_filter].name}]"\n        else:\n            file_label = "[All files]"\n        try:\n            return render_global_search(\n                width,\n                height,\n                query=self.global_search_buffer,\n                mode=self.global_search_mode,\n                mode_labels=(\n                    (SEARCH_SIMPLE, "Simple"),\n                    (SEARCH_REGEX, "Regex"),\n                    (SEARCH_BOOLEAN, "Boolean"),\n                    (SEARCH_FUZZY, "Fuzzy"),\n                ),\n                ignore_case=self.global_search_ignore_case,\n                sort_mode=self.global_search_sort,\n                file_filter_label=file_label,\n                results=self.global_search_results,\n                selected=self.global_search_selected,\n                truncated=self.global_search_truncated,\n                error=self.global_search_error,\n                panes=self.panes,\n                preview_enabled=self.global_search_preview,\n                color=self.color,\n            )\n        except Exception as exc:\n            self.global_search_error = f"{type(exc).__name__}: {exc}"\n            return _panel_lines(\n                "Global search",\n                ["Search rendering error:", self.global_search_error, "", "Esc close"],\n                width,\n                height,\n                self.color,\n            )\n\n''',
    "global search render guard",
)
replace_once(
    APP,
    '''        if self.help_active:\n            if key == "?":\n                self.help_active = False\n                self.dirty = True\n            return False\n''',
    '''        if self.help_active:\n            if key in ("?", "ESC"):\n                self.help_active = False\n                self.dirty = True\n            elif key in ("q", "Q"):\n                return True\n            return False\n''',
    "help Esc",
)
replace_once(
    APP,
    '''        if self.update_confirm_active:\n            if key in ("n", "N", "ESC", "q", "Q"):\n                self.update_confirm_active = False\n                self.set_message("update cancelled")\n            elif key in ("y", "Y") and self.update_release is not None and not self.update_installing:\n''',
    '''        if self.update_confirm_active:\n            if self.update_installing:\n                return False\n            if key in ("n", "N", "ESC", "q", "Q"):\n                self.update_confirm_active = False\n                self.set_message("update cancelled")\n            elif key in ("y", "Y") and self.update_release is not None:\n''',
    "active update lock",
)
replace_once(
    APP,
    '''            if stage.startswith("Downloading"):\n                if current is not None and total and total > 0:\n                    self.update_overall_progress = 0.05 + 0.70 * max(0.0, min(1.0, current / total))\n                else:\n                    self.update_overall_progress = max(self.update_overall_progress, 0.10)\n            elif stage.startswith("Verifying"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.80)\n            elif stage.startswith("Backing up"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.90)\n            elif stage.startswith("Installing") or stage.startswith("Replacing"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.97)\n            else:\n                self.update_overall_progress = max(self.update_overall_progress, 0.02)\n''',
    '''            if stage.startswith("Downloading release"):\n                if current is not None and total and total > 0:\n                    self.update_overall_progress = 0.03 + 0.37 * max(0.0, min(1.0, current / total))\n                else:\n                    self.update_overall_progress = max(self.update_overall_progress, 0.08)\n            elif stage.startswith("Verifying release"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.42)\n            elif stage.startswith("Downloading runtime"):\n                if current is not None and total and total > 0:\n                    self.update_overall_progress = 0.45 + 0.30 * max(0.0, min(1.0, current / total))\n                else:\n                    self.update_overall_progress = max(self.update_overall_progress, 0.50)\n            elif stage.startswith("Verifying runtime"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.77)\n            elif stage.startswith("Unpacking runtime"):\n                if current is not None and total and total > 0:\n                    self.update_overall_progress = 0.80 + 0.14 * max(0.0, min(1.0, current / total))\n                else:\n                    self.update_overall_progress = max(self.update_overall_progress, 0.84)\n            elif stage.startswith("Runtime already"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.94)\n            elif stage.startswith("Backing up"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.96)\n            elif stage.startswith("Installing") or stage.startswith("Replacing"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.98)\n            else:\n                self.update_overall_progress = max(self.update_overall_progress, 0.02)\n''',
    "update progress stages",
)

# Universal core contains only htail Python code; native runtimes are separate assets.
Path("tools/build_release.py").write_text(r'''#!/usr/bin/env python3
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
        _write(archive, "bundle.json", json.dumps({
            "format": 3,
            "platform": "linux-x86_64",
            "runtime_id": runtime_id(),
            "supported_cpython_abis": list(SUPPORTED_ABIS),
        }, indent=2, sort_keys=True).encode())
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
    # Normal self-updates prepare this before restart. This path exists only
    # for a fresh manual install or recovery of a missing cache.
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "htail")
    args = parser.parse_args()
    wrapper = build_wrapper(read_version(), build_payload())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(wrapper, encoding="utf-8", newline="\n")
    args.output.chmod(0o755)
    print(f"built htail {read_version()}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''', encoding="utf-8")

Path("tools/build_runtime.py").write_text(r'''#!/usr/bin/env python3
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


def runtime_id():
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def download_wheels(abi, target):
    pyver = abi.removeprefix("cp")
    subprocess.run([
        sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
        "--only-binary=:all:", "--implementation=cp", f"--python-version={pyver}",
        f"--abi={abi}", f"--platform={PLATFORM}", "--dest", str(target),
        "--requirement", str(REQUIREMENTS),
    ], check=True)
    wheels = sorted(target.glob("*.whl"))
    if not wheels:
        raise SystemExit(f"no wheels resolved for {abi}")
    return wheels


def build_one(abi, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"htail-runtime-{abi}.zip"
    with tempfile.TemporaryDirectory(prefix=f"htail-runtime-{abi}-") as td:
        wheels = download_wheels(abi, Path(td))
        manifest = {"format": 1, "runtime_id": runtime_id(), "abi": abi, "platform": PLATFORM, "wheels": [w.name for w in wheels]}
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("runtime.json", json.dumps(manifest, indent=2, sort_keys=True), compress_type=zipfile.ZIP_DEFLATED)
            for wheel in wheels:
                archive.writestr("wheels/" + wheel.name, wheel.read_bytes(), compress_type=zipfile.ZIP_STORED)
    print(f"built {output} ({output.stat().st_size:,} bytes)")
    return output


def self_test(path):
    with zipfile.ZipFile(path) as outer:
        manifest = json.loads(outer.read("runtime.json"))
        with tempfile.TemporaryDirectory(prefix="htail-runtime-test-") as td:
            target = Path(td)
            for wheel_name in manifest["wheels"]:
                with zipfile.ZipFile(io.BytesIO(outer.read("wheels/" + wheel_name))) as wheel:
                    wheel.extractall(target)
            env = dict(os.environ)
            env["PYTHONPATH"] = str(target)
            subprocess.run([sys.executable, "-c", "import rapidfuzz; print(rapidfuzz.__version__)"], check=True, env=env)


def main():
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
''', encoding="utf-8")

CORE = "src/htail_app/core.py"
replace_once(CORE, "import urllib.request\n", "import urllib.request\nimport zipfile\nimport io\n", "runtime imports")
replace_once(
    CORE,
    '''class ReleaseInfo:\n    version: str\n    tag: str\n    asset_url: str\n    asset_name: str\n    checksum_url: Optional[str] = None\n    notes: str = ""\n''',
    '''class ReleaseInfo:\n    version: str\n    tag: str\n    asset_url: str\n    asset_name: str\n    checksum_url: Optional[str] = None\n    notes: str = ""\n    runtime_url: Optional[str] = None\n    runtime_checksum_url: Optional[str] = None\n    runtime_abi: Optional[str] = None\n''',
    "ReleaseInfo runtime fields",
)
replace_once(
    CORE,
    "\n\nclass UpdateService:\n",
    r'''

def current_cpython_abi() -> str:
    return f"cp{sys.version_info.major}{sys.version_info.minor}"


def runtime_cache_dir(runtime_id: str, abi: str) -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "htail"
    return root / "runtime" / runtime_id / abi


def _runtime_id_from_source(source: str) -> Optional[str]:
    match = re.search(r'^HTAIL_RUNTIME_ID\s*=\s*"([0-9a-fA-F]{64})"', source, re.MULTILINE)
    return match.group(1).lower() if match else None


def _install_runtime_bundle(content: bytes, target: Path, runtime_id: str, abi: str, report) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as outer:
        manifest = json.loads(outer.read("runtime.json").decode("utf-8"))
        if manifest.get("runtime_id") != runtime_id:
            raise RuntimeError("runtime bundle id does not match htail core")
        if manifest.get("abi") != abi:
            raise RuntimeError(f"runtime bundle ABI {manifest.get('abi')!r} does not match {abi}")
        payloads = []
        total = 0
        for wheel_name in manifest.get("wheels") or []:
            payload = outer.read("wheels/" + wheel_name)
            with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
                total += sum(item.file_size for item in wheel.infolist() if not item.is_dir())
            payloads.append(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{abi}-", dir=str(target.parent)))
    current = 0
    try:
        report(f"Unpacking runtime {abi}…", current, total)
        for payload in payloads:
            with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
                for item in wheel.infolist():
                    if item.is_dir():
                        continue
                    destination = (temp / item.filename).resolve()
                    if temp.resolve() not in destination.parents:
                        raise RuntimeError(f"unsafe runtime path: {item.filename}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with wheel.open(item) as src, destination.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    mode = (item.external_attr >> 16) & 0o777
                    if mode:
                        os.chmod(destination, mode)
                    current += item.file_size
                    report(f"Unpacking runtime {abi}…", current, total)
        try:
            os.replace(temp, target)
        except OSError:
            if not target.is_dir():
                raise
    finally:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)


class UpdateService:
''',
    "runtime helpers",
)
replace_once(
    CORE,
    '''        assets = payload.get("assets") or []\n        asset_url: Optional[str] = None\n        checksum_url: Optional[str] = None\n        for asset in assets:\n            name = str(asset.get("name") or "")\n            url = str(asset.get("browser_download_url") or "")\n            if name == self.asset_name and url:\n                asset_url = url\n            elif name in (f"{self.asset_name}.sha256", f"{self.asset_name}.sha256sum") and url:\n                checksum_url = url\n''',
    '''        assets = payload.get("assets") or []\n        asset_url: Optional[str] = None\n        checksum_url: Optional[str] = None\n        runtime_abi = current_cpython_abi()\n        runtime_name = f"htail-runtime-{runtime_abi}.zip"\n        runtime_url: Optional[str] = None\n        runtime_checksum_url: Optional[str] = None\n        for asset in assets:\n            name = str(asset.get("name") or "")\n            url = str(asset.get("browser_download_url") or "")\n            if name == self.asset_name and url:\n                asset_url = url\n            elif name in (f"{self.asset_name}.sha256", f"{self.asset_name}.sha256sum") and url:\n                checksum_url = url\n            elif name == runtime_name and url:\n                runtime_url = url\n            elif name in (f"{runtime_name}.sha256", f"{runtime_name}.sha256sum") and url:\n                runtime_checksum_url = url\n''',
    "runtime asset lookup",
)
replace_once(
    CORE,
    '''            checksum_url=checksum_url,\n            notes=notes,\n        )\n''',
    '''            checksum_url=checksum_url,\n            notes=notes,\n            runtime_url=runtime_url,\n            runtime_checksum_url=runtime_checksum_url,\n            runtime_abi=runtime_abi,\n        )\n''',
    "runtime metadata return",
)
replace_once(CORE, '            report("Verifying SHA-256 checksum…")\n', '            report("Verifying release SHA-256 checksum…")\n', "release verify label")
replace_once(
    CORE,
    '''            try:\n                compile(source, str(target), "exec")\n            except SyntaxError as exc:\n                return False, f"downloaded update failed syntax validation: {exc}"\n\n            report("Preparing update…")\n''',
    '''            try:\n                compile(source, str(target), "exec")\n            except SyntaxError as exc:\n                return False, f"downloaded update failed syntax validation: {exc}"\n\n            runtime_id = _runtime_id_from_source(source)\n            if runtime_id:\n                abi = release.runtime_abi or current_cpython_abi()\n                runtime_target = runtime_cache_dir(runtime_id, abi)\n                if runtime_target.is_dir():\n                    report(f"Runtime already prepared ({abi})…")\n                else:\n                    if not release.runtime_url or not release.runtime_checksum_url:\n                        return False, f"release {release.tag} has no runtime asset for {abi}"\n                    runtime_request = urllib.request.Request(release.runtime_url, headers={"User-Agent": f"htail/{HTAIL_VERSION}"})\n                    with urllib.request.urlopen(runtime_request, timeout=20.0) as response:\n                        headers = getattr(response, "headers", {})\n                        size = headers.get("Content-Length") if hasattr(headers, "get") else None\n                        runtime_total = int(size) if size and size.isdigit() else None\n                        runtime_chunks = []\n                        runtime_current = 0\n                        report(f"Downloading runtime {abi}…", runtime_current, runtime_total)\n                        while True:\n                            try:\n                                chunk = response.read(65536)\n                            except TypeError:\n                                chunk = response.read()\n                            if not chunk:\n                                break\n                            runtime_chunks.append(chunk)\n                            runtime_current += len(chunk)\n                            report(f"Downloading runtime {abi}…", runtime_current, runtime_total)\n                        runtime_content = b"".join(runtime_chunks)\n                    report(f"Verifying runtime {abi}…")\n                    checksum_request = urllib.request.Request(release.runtime_checksum_url, headers={"User-Agent": f"htail/{HTAIL_VERSION}"})\n                    with urllib.request.urlopen(checksum_request, timeout=10.0) as response:\n                        checksum_text = response.read().decode("utf-8", errors="replace")\n                    checksum_match = re.search(r"\\b([0-9a-fA-F]{64})\\b", checksum_text)\n                    if not checksum_match:\n                        return False, "runtime checksum asset does not contain a SHA-256 digest"\n                    if hashlib.sha256(runtime_content).hexdigest() != checksum_match.group(1).lower():\n                        return False, "downloaded runtime failed SHA-256 verification"\n                    _install_runtime_bundle(runtime_content, runtime_target, runtime_id, abi, report)\n\n            report("Preparing update…")\n''',
    "prepare matching runtime before restart",
)

# CI: core build is tiny; validate one ABI runtime only.
replace_once(
    ".github/workflows/ci.yml",
    '''      - uses: actions/setup-python@v5\n        with:\n          python-version: "3.13"\n''',
    '''      - uses: actions/setup-python@v5\n        with:\n          python-version: "3.13"\n          cache: "pip"\n          cache-dependency-path: tools/bundle-requirements.txt\n''',
    "CI pip cache",
)
replace_once(
    ".github/workflows/ci.yml",
    '''      - name: Build standalone executable\n        run: python tools/build_release.py --output /tmp/htail\n      - name: Smoke test bundle\n''',
    '''      - name: Build standalone executable\n        run: python tools/build_release.py --output /tmp/htail\n      - name: Build current-ABI runtime\n        run: python tools/build_runtime.py --output-dir /tmp/runtime --abi cp313\n      - name: Smoke test current-ABI runtime\n        run: python tools/build_runtime.py --self-test /tmp/runtime/htail-runtime-cp313.zip\n      - name: Smoke test bundle\n''',
    "CI runtime asset",
)

# Release: publish five small ABI-specific runtime assets alongside the core.
replace_once(
    ".github/workflows/release.yml",
    '''      - name: Set up Python\n        uses: actions/setup-python@v5\n        with:\n          python-version: "3.13"\n''',
    '''      - name: Set up Python\n        uses: actions/setup-python@v5\n        with:\n          python-version: "3.13"\n          cache: "pip"\n          cache-dependency-path: tools/bundle-requirements.txt\n''',
    "release pip cache",
)
replace_once(
    ".github/workflows/release.yml",
    '''      - name: Build standalone executable\n        run: python tools/build_release.py --output dist/htail\n''',
    '''      - name: Build standalone executable\n        run: python tools/build_release.py --output dist/htail\n\n      - name: Build native runtime assets\n        run: python tools/build_runtime.py --output-dir dist\n''',
    "release runtimes",
)
replace_once(
    ".github/workflows/release.yml",
    '''      - name: Smoke test bundled native dependencies\n        run: python dist/htail --bundle-self-test\n''',
    '''      - name: Smoke test native runtime asset\n        run: python tools/build_runtime.py --self-test dist/htail-runtime-cp313.zip\n''',
    "release runtime smoke",
)
replace_once(
    ".github/workflows/release.yml",
    '''      - name: Prepare release assets\n        if: steps.existing.outputs.exists != 'true'\n        run: |\n          chmod +x dist/htail\n          sha256sum dist/htail > dist/htail.sha256\n''',
    '''      - name: Prepare release assets\n        if: steps.existing.outputs.exists != 'true'\n        run: |\n          chmod +x dist/htail\n          sha256sum dist/htail > dist/htail.sha256\n          for runtime in dist/htail-runtime-*.zip; do\n            sha256sum "$runtime" > "$runtime.sha256"\n          done\n''',
    "runtime checksums",
)
replace_once(
    ".github/workflows/release.yml",
    '''            --notes-file RELEASE_NOTES.md \\\n            dist/htail dist/htail.sha256\n''',
    '''            --notes-file RELEASE_NOTES.md \\\n            dist/htail dist/htail.sha256 \\\n            dist/htail-runtime-*.zip dist/htail-runtime-*.zip.sha256\n''',
    "runtime release assets",
)

replace_once("src/htail_app/__init__.py", 'VERSION = "0.16.0"\n', 'VERSION = "0.16.1"\n', "version")
Path("RELEASE_NOTES.md").write_text('''# htail 0.16.1\n\n## Bug fixes\n\n- Fixed standalone `Esc` on POSIX/WSL terminals; escape-sequence parsing no longer blocks waiting for another key. Esc now reliably closes all dismissible modals.\n- Global-search backend/rendering failures are contained inside the search workspace instead of terminating htail.\n\n## Distribution and CI\n\n- Split native dependencies out of the universal htail core. Releases now publish a small core plus `htail-runtime-cp310.zip` through `cp314.zip`; the updater downloads only the runtime matching the Python currently running htail.\n- Runtime download, checksum verification and unpacking happen before restart and are visible in the update progress modal.\n- Runtime caches are keyed by the dependency-manifest hash, so subsequent htail updates reuse the existing native runtime when dependencies are unchanged.\n- 0.16.1 can reuse an already-extracted 0.16.0 RapidFuzz runtime for the transition. Fresh manual installs bootstrap only their matching ABI.\n- Normal CI builds only cp313; release builds create all supported runtime assets. Wheels are stored without recompression, removing the long silent DEFLATE-9 build phase from 0.16.0.\n''', encoding="utf-8")

Path("tests/test_esc_global_0161.py").write_text('''from __future__ import annotations\n\nfrom pathlib import Path\nimport tempfile\nimport unittest\n\nfrom htail_app import app, core\nfrom htail_app.app import MultiApp\nfrom htail_app.input import InputReader, parse_escape_sequence\n\n\nclass EscapeReaderTests(unittest.TestCase):\n    def test_lone_escape_never_performs_blocking_continuation_read(self):\n        reader = InputReader()\n        reader._fd = 123\n        reader._input_ready = lambda timeout: False\n        reader._read_byte = lambda: self.fail("unexpected read")\n        self.assertEqual(parse_escape_sequence(reader._read_escape_sequence(b"\\x1b")), "ESC")\n\n\nclass ModalEscapeTests(unittest.TestCase):\n    def make_app(self, root, color=False):\n        source = Path(root) / "coord.md"\n        source.write_text("# Coordination\\nWorkflow verification remains authoritative.\\n", encoding="utf-8")\n        argv = [str(source), "--no-native-watch"]\n        if not color:\n            argv.append("--no-color")\n        return MultiApp(app.parse_args(argv), color, core.DisplayFilter(), core.UpdateService(""))\n\n    def test_escape_closes_all_dismissible_modals(self):\n        with tempfile.TemporaryDirectory() as td:\n            a = self.make_app(td)\n            try:\n                a.handle_input(":"); self.assertTrue(a.palette_active); a.handle_input("ESC"); self.assertFalse(a.palette_active)\n                a.handle_input("g"); self.assertTrue(a.global_search_active); a.handle_input("ESC"); self.assertFalse(a.global_search_active)\n                a.handle_input("/"); a.handle_input("ESC"); self.assertIsNone(a.prompt_mode)\n                a.handle_input("h"); a.handle_input("ESC"); self.assertIsNone(a.prompt_mode)\n                a.handle_input("l"); a.handle_input("ESC"); self.assertFalse(a.layout_menu)\n                a.handle_input("?"); a.handle_input("ESC"); self.assertFalse(a.help_active)\n                a.update_confirm_active = True; a.handle_input("ESC"); self.assertFalse(a.update_confirm_active)\n            finally:\n                a.close_native_watch()\n\n    def test_typing_global_search_with_color_cannot_exit_viewer(self):\n        with tempfile.TemporaryDirectory() as td:\n            a = self.make_app(td, color=True)\n            try:\n                a.handle_input("g")\n                a.handle_input("v")\n                self.assertTrue(a.global_search_active)\n                width, frame = a._frame_rows()\n                self.assertGreater(width, 0)\n                self.assertIn("Global search", "\\n".join(core.strip_ansi(row) for row in frame))\n            finally:\n                a.close_native_watch()\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")

replace_once(
    "tests/test_bundle.py",
    '''                    str(ROOT / "tools" / "build_release.py"),\n                    "--no-vendor",\n                    "--output",\n''',
    '''                    str(ROOT / "tools" / "build_release.py"),\n                    "--output",\n''',
    "bundle unit CLI",
)
