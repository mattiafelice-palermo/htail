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


# --- POSIX Esc: never perform a blocking second read after the ESC byte. ---
INPUT = "src/htail_app/input.py"
replace_once(
    INPUT,
    '''    def _read_escape_sequence(self, first: bytes) -> str:\n        seq = first.decode("utf-8", errors="ignore")\n        deadline = time.monotonic() + 0.003\n        while time.monotonic() < deadline:\n            byte = self._read_byte()\n            if not byte:\n                time.sleep(0.0002)\n                continue\n            seq += byte.decode("utf-8", errors="ignore")\n            if seq.endswith(("~", "A", "B", "C", "D", "H", "F", "M", "m", "Z")):\n                break\n        return seq\n''',
    '''    def _input_ready(self, timeout: float) -> bool:\n        if self._fd is None:\n            return False\n        try:\n            import select\n            ready, _, _ = select.select([self._fd], [], [], max(0.0, timeout))\n            return bool(ready)\n        except Exception:\n            return False\n\n    def _read_escape_sequence(self, first: bytes) -> str:\n        seq = first.decode("utf-8", errors="ignore")\n        # ESC is both a complete key and the prefix for arrows/mouse input.\n        # Wait briefly for continuation bytes, but only read after select says\n        # the fd is ready.  Calling blocking os.read() here made a lone Esc\n        # wait indefinitely for the next key on POSIX/WSL terminals.\n        deadline = time.monotonic() + 0.010\n        while True:\n            remaining = deadline - time.monotonic()\n            if remaining <= 0 or not self._input_ready(remaining):\n                break\n            byte = self._read_byte()\n            if not byte:\n                break\n            seq += byte.decode("utf-8", errors="ignore")\n            if seq.endswith(("~", "A", "B", "C", "D", "H", "F", "M", "m", "Z")):\n                break\n        return seq\n''',
    "nonblocking POSIX escape sequence",
)

# --- Modal behavior + global-search crash containment. ---
APP = "src/htail_app/app.py"
replace_between(
    APP,
    "    def _refresh_global_search_results(self) -> None:\n",
    "    def _cycle_global_search_file_filter(self, backwards: bool = False) -> None:\n",
    '''    def _refresh_global_search_results(self) -> None:\n        signature, corpus = self._global_search_corpus_data()\n        key = (\n            self.global_search_buffer,\n            self.global_search_mode,\n            self.global_search_ignore_case,\n            self.global_search_sort,\n            self.global_search_file_filter,\n            signature,\n        )\n        if key == self._global_search_cache_key:\n            return\n        self._global_search_cache_key = key\n        try:\n            page = search_corpus(\n                corpus,\n                self.global_search_buffer,\n                self.global_search_mode,\n                self._global_search_flags(),\n                file_filter=self.global_search_file_filter,\n                sort_mode=self.global_search_sort,\n                limit=GLOBAL_SEARCH_LIMIT,\n            )\n        except Exception as exc:\n            # A search query must never terminate the viewer.  Keep the modal\n            # open and surface the concrete backend/search failure in-place.\n            self.global_search_results = []\n            self.global_search_error = f"{type(exc).__name__}: {exc}"\n            self.global_search_truncated = False\n            self.global_search_selected = 0\n            return\n        self.global_search_results = page.results\n        self.global_search_error = page.error\n        self.global_search_truncated = page.truncated\n        if self.global_search_results:\n            self.global_search_selected = min(max(0, self.global_search_selected), len(self.global_search_results) - 1)\n        else:\n            self.global_search_selected = 0\n\n''',
    "global search backend guard",
)

replace_between(
    APP,
    "    def _global_search_lines(self, width: int, height: int) -> List[str]:\n",
    "    def _select_global_search_result(self) -> bool:\n",
    '''    def _global_search_lines(self, width: int, height: int) -> List[str]:\n        self._refresh_global_search_results()\n        if self.global_search_file_filter is None:\n            file_label = "[All files]"\n        elif 0 <= self.global_search_file_filter < len(self.panes):\n            file_label = f"[{self.panes[self.global_search_file_filter].name}]"\n        else:\n            file_label = "[All files]"\n        try:\n            return render_global_search(\n                width,\n                height,\n                query=self.global_search_buffer,\n                mode=self.global_search_mode,\n                mode_labels=(\n                    (SEARCH_SIMPLE, "Simple"),\n                    (SEARCH_REGEX, "Regex"),\n                    (SEARCH_BOOLEAN, "Boolean"),\n                    (SEARCH_FUZZY, "Fuzzy"),\n                ),\n                ignore_case=self.global_search_ignore_case,\n                sort_mode=self.global_search_sort,\n                file_filter_label=file_label,\n                results=self.global_search_results,\n                selected=self.global_search_selected,\n                truncated=self.global_search_truncated,\n                error=self.global_search_error,\n                panes=self.panes,\n                preview_enabled=self.global_search_preview,\n                color=self.color,\n            )\n        except Exception as exc:\n            # Rendering malformed or unexpected source text should be equally\n            # non-fatal.  Keep a usable escape route and diagnostic visible.\n            self.global_search_error = f"{type(exc).__name__}: {exc}"\n            return _panel_lines(\n                "Global search",\n                ["Search rendering error:", self.global_search_error, "", "Esc close"],\n                width,\n                height,\n                self.color,\n            )\n\n''',
    "global search render guard",
)

replace_once(
    APP,
    '''        if self.help_active:\n            if key == "?":\n                self.help_active = False\n                self.dirty = True\n            return False\n''',
    '''        if self.help_active:\n            if key in ("?", "ESC"):\n                self.help_active = False\n                self.dirty = True\n            elif key in ("q", "Q"):\n                return True\n            return False\n''',
    "help Esc and q",
)

# --- Release bundler: store wheels as wheels; unpack one ABI lazily at runtime. ---
BUILD = "tools/build_release.py"
replace_once(
    BUILD,
    '''def _write_deterministic(archive: zipfile.ZipFile, name: str, data: bytes, executable: bool = False) -> None:\n    info = zipfile.ZipInfo(name.replace(os.sep, "/"), _FIXED_ZIP_TIME)\n    info.compress_type = zipfile.ZIP_DEFLATED\n    info.external_attr = ((0o755 if executable else 0o644) & 0xFFFF) << 16\n    archive.writestr(info, data)\n''',
    '''def _write_deterministic(\n    archive: zipfile.ZipFile,\n    name: str,\n    data: bytes,\n    executable: bool = False,\n    compress_type: int = zipfile.ZIP_DEFLATED,\n) -> None:\n    info = zipfile.ZipInfo(name.replace(os.sep, "/"), _FIXED_ZIP_TIME)\n    info.compress_type = compress_type\n    info.external_attr = ((0o755 if executable else 0o644) & 0xFFFF) << 16\n    archive.writestr(info, data)\n''',
    "deterministic writer compression",
)
replace_once(BUILD, "def build_payload(include_vendor: bool = True) -> bytes:\n", "def build_payload(include_vendor: bool = True, abis=SUPPORTED_CPYTHON_ABIS) -> bytes:\n", "build payload ABI arg")
replace_once(BUILD, '            "supported_cpython_abis": list(SUPPORTED_CPYTHON_ABIS),\n', '            "supported_cpython_abis": list(abis),\n', "manifest ABIs")
old_vendor = '''                for abi in SUPPORTED_CPYTHON_ABIS:\n                    wheel_names = []\n                    seen_paths = set()\n                    for wheel in _download_wheels(abi, wheel_root):\n                        wheel_names.append(wheel.name)\n                        with zipfile.ZipFile(wheel) as package:\n                            for entry in sorted(package.infolist(), key=lambda item: item.filename):\n                                if entry.is_dir():\n                                    continue\n                                destination = str(Path("vendor") / abi / entry.filename)\n                                if destination in seen_paths:\n                                    raise SystemExit(f"duplicate bundled path for {abi}: {entry.filename}")\n                                seen_paths.add(destination)\n                                _write_deterministic(\n                                    archive,\n                                    destination,\n                                    package.read(entry.filename),\n                                    executable=bool((entry.external_attr >> 16) & 0o111),\n                                )\n                    manifest["vendor"][abi] = {"wheels": wheel_names}\n'''
new_vendor = '''                for abi in abis:\n                    wheel_names = []\n                    for wheel in _download_wheels(abi, wheel_root):\n                        wheel_names.append(wheel.name)\n                        # Wheels are already ZIP-compressed.  Embedding the wheel\n                        # bytes verbatim avoids the expensive unzip + DEFLATE-9\n                        # recompression that dominated 0.16 CI/release builds.\n                        _write_deterministic(\n                            archive,\n                            str(Path("wheels") / abi / wheel.name),\n                            wheel.read_bytes(),\n                            compress_type=zipfile.ZIP_STORED,\n                        )\n                    manifest["vendor"][abi] = {"wheels": wheel_names}\n'''
replace_once(BUILD, old_vendor, new_vendor, "store wheels directly")

marker = '''def _main():\n    if sys.argv[1:] == ["--version"]:\n'''
insert = '''def _extract_vendor(env_dir: Path, abi: str) -> Path:\n    target = env_dir / "vendor" / abi\n    if target.is_dir():\n        return target\n    wheel_dir = env_dir / "wheels" / abi\n    wheels = sorted(wheel_dir.glob("*.whl")) if wheel_dir.is_dir() else []\n    if not wheels:\n        return target\n    vendor_root = target.parent\n    vendor_root.mkdir(parents=True, exist_ok=True)\n    temp = Path(tempfile.mkdtemp(prefix=f".{abi}-", dir=str(vendor_root)))\n    try:\n        for wheel in wheels:\n            with zipfile.ZipFile(wheel) as package:\n                package.extractall(temp)\n        try:\n            os.replace(temp, target)\n        except OSError:\n            if not target.is_dir():\n                raise\n        return target\n    finally:\n        if temp.exists():\n            shutil.rmtree(temp, ignore_errors=True)\n\n\ndef _main():\n    if sys.argv[1:] == ["--version"]:\n'''
replace_once(BUILD, marker, insert, "lazy vendor extractor")
replace_once(
    BUILD,
    '''    vendor_dir = env_dir / "vendor" / abi\n    python_paths = [str(app_dir)]\n    if sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64") and vendor_dir.is_dir():\n        python_paths.insert(0, str(vendor_dir))\n''',
    '''    python_paths = [str(app_dir)]\n    if sys.platform.startswith("linux") and platform.machine().lower() in ("x86_64", "amd64"):\n        vendor_dir = _extract_vendor(env_dir, abi)\n        if vendor_dir.is_dir():\n            python_paths.insert(0, str(vendor_dir))\n''',
    "runtime lazy vendor selection",
)
replace_once(
    BUILD,
    '''    parser.add_argument("--no-vendor", action="store_true", help="development-only: omit bundled runtime dependencies")\n    args = parser.parse_args()\n    version = read_version()\n    wrapper = build_wrapper(version, build_payload(include_vendor=not args.no_vendor))\n''',
    '''    parser.add_argument("--no-vendor", action="store_true", help="development-only: omit bundled runtime dependencies")\n    parser.add_argument("--abi", action="append", choices=SUPPORTED_CPYTHON_ABIS, dest="abis", help="bundle only this CPython ABI; repeatable")\n    args = parser.parse_args()\n    version = read_version()\n    abis = tuple(args.abis) if args.abis else SUPPORTED_CPYTHON_ABIS\n    wrapper = build_wrapper(version, build_payload(include_vendor=not args.no_vendor, abis=abis))\n''',
    "builder ABI CLI",
)

# CI validates one native ABI; releases still build every supported ABI.
CI = ".github/workflows/ci.yml"
replace_once(
    CI,
    '''      - uses: actions/setup-python@v5\n        with:\n          python-version: "3.13"\n''',
    '''      - uses: actions/setup-python@v5\n        with:\n          python-version: "3.13"\n          cache: "pip"\n          cache-dependency-path: tools/bundle-requirements.txt\n''',
    "pip cache",
)
replace_once(CI, "        run: python tools/build_release.py --output /tmp/htail\n", "        run: python tools/build_release.py --output /tmp/htail --abi cp313\n", "single ABI CI build")

# Version + release notes.
replace_once("src/htail_app/__init__.py", 'VERSION = "0.16.0"\n', 'VERSION = "0.16.1"\n', "version")
Path("RELEASE_NOTES.md").write_text('''# htail 0.16.1\n\n## Bug fixes\n\n- Fixed standalone `Esc` on POSIX/WSL terminals. Escape-sequence parsing now waits for continuation bytes with `select()` instead of performing a blocking second read, so Esc reliably closes the command palette, global search, local search/highlight, layout chooser and update confirmation. Help now closes with Esc as well.\n- Global search backend and rendering errors are contained inside the search workspace instead of terminating htail, with the concrete error shown in-place.\n- Added regression coverage for typing/rendering in the 0.16 global-search workspace with color enabled.\n\n## Build performance\n\n- Native dependency wheels are now embedded in their already-compressed wheel form and lazily extracted only for the running CPython ABI on first launch. This removes the expensive build-time unzip/recompress pass introduced in 0.16.\n- Normal CI validates the current CPython 3.13 native bundle only, while release builds continue to include CPython 3.10–3.14.\n- GitHub Actions now caches pip downloads using the bundle dependency manifest.\n''', encoding="utf-8")

# Regression tests kept separate so the change is easy to audit.
Path("tests/test_esc_global_0161.py").write_text(r'''from __future__ import annotations\n\nfrom pathlib import Path\nimport tempfile\nimport unittest\n\nfrom htail_app import app, core\nfrom htail_app.app import MultiApp\nfrom htail_app.input import InputReader, parse_escape_sequence\n\n\nclass EscapeReaderTests(unittest.TestCase):\n    def test_lone_escape_does_not_attempt_blocking_continuation_read(self):\n        reader = InputReader()\n        reader._fd = 123\n        reader._input_ready = lambda timeout: False\n        reader._read_byte = lambda: self.fail("read must not occur when continuation is not ready")\n        sequence = reader._read_escape_sequence(b"\\x1b")\n        self.assertEqual(parse_escape_sequence(sequence), "ESC")\n\n\nclass ModalEscapeTests(unittest.TestCase):\n    def make_app(self, root: Path, color=False):\n        source = root / "coord.md"\n        source.write_text(\n            "# Coordination\\n"\n            "Workflow state is authoritative in `state.json`.\\n"\n            "Detailed verification evidence remains available.\\n",\n            encoding="utf-8",\n        )\n        args = app.parse_args([str(source), "--no-native-watch"] + (["--no-color"] if not color else []))\n        return MultiApp(args, color, core.DisplayFilter(), core.UpdateService(""))\n\n    def test_escape_closes_every_dismissible_modal(self):\n        with tempfile.TemporaryDirectory() as td:\n            application = self.make_app(Path(td))\n            try:\n                application.handle_input(":")\n                self.assertTrue(application.palette_active)\n                application.handle_input("ESC")\n                self.assertFalse(application.palette_active)\n\n                application.handle_input("g")\n                self.assertTrue(application.global_search_active)\n                application.handle_input("ESC")\n                self.assertFalse(application.global_search_active)\n\n                application.handle_input("/")\n                self.assertEqual(application.prompt_mode, "search")\n                application.handle_input("ESC")\n                self.assertIsNone(application.prompt_mode)\n\n                application.handle_input("h")\n                self.assertEqual(application.prompt_mode, "highlight")\n                application.handle_input("ESC")\n                self.assertIsNone(application.prompt_mode)\n\n                application.handle_input("l")\n                self.assertTrue(application.layout_menu)\n                application.handle_input("ESC")\n                self.assertFalse(application.layout_menu)\n\n                application.handle_input("?")\n                self.assertTrue(application.help_active)\n                application.handle_input("ESC")\n                self.assertFalse(application.help_active)\n\n                application.update_confirm_active = True\n                application.handle_input("ESC")\n                self.assertFalse(application.update_confirm_active)\n            finally:\n                application.close_native_watch()\n\n    def test_global_search_typing_and_color_render_stay_inside_workspace(self):\n        with tempfile.TemporaryDirectory() as td:\n            application = self.make_app(Path(td), color=True)\n            try:\n                application.handle_input("g")\n                application.handle_input("v")\n                self.assertTrue(application.global_search_active)\n                self.assertIsNone(application.global_search_error)\n                width, frame = application._frame_rows()\n                self.assertGreater(width, 0)\n                screen = "\\n".join(core.strip_ansi(row) for row in frame)\n                self.assertIn("Global search", screen)\n                self.assertIn("verification", screen.lower())\n            finally:\n                application.close_native_watch()\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")
