from pathlib import Path

# Version.
p = Path('src/htail_app/__init__.py')
t = p.read_text()
t = t.replace('VERSION = "0.8.3"', 'VERSION = "0.8.4"')
p.write_text(t)

# Core updater: add progress callback and stream the download.
p = Path('src/htail_app/core.py')
t = p.read_text()
t = t.replace(
    'from typing import Iterable, List, Optional, Pattern, Sequence, Tuple',
    'from typing import Callable, Iterable, List, Optional, Pattern, Sequence, Tuple',
)
start = t.index('    def install(self, release: ReleaseInfo, target: Path) -> Tuple[bool, str]:')
end = t.index('\n\n# ---------------------------------------------------------------------------\n# Self-install support', start)
method = '''    def install(
        self,
        release: ReleaseInfo,
        target: Path,
        progress: Optional[Callable[[str, Optional[int], Optional[int]], None]] = None,
    ) -> Tuple[bool, str]:
        """Download, validate and atomically replace the running script.

        ``progress`` receives ``(stage, current_bytes, total_bytes)``. Byte
        counts are supplied while downloading; later validation/install stages
        use ``None`` counts. Progress callbacks are best-effort and can never
        abort an otherwise valid update.
        """
        target = target.resolve()
        target_dir = target.parent
        if not target.exists():
            return False, f"cannot locate running script: {target}"
        if not os.access(target, os.W_OK):
            return False, f"running script is not writable: {target}"

        def report(stage: str, current: Optional[int] = None, total: Optional[int] = None) -> None:
            if progress is None:
                return
            try:
                progress(stage, current, total)
            except Exception:
                pass

        temp_path: Optional[Path] = None
        try:
            request = urllib.request.Request(
                release.asset_url,
                headers={"User-Agent": f"htail/{HTAIL_VERSION}"},
            )
            with urllib.request.urlopen(request, timeout=15.0) as response:
                total_header = response.headers.get('Content-Length')
                total = int(total_header) if total_header and total_header.isdigit() else None
                chunks: List[bytes] = []
                current = 0
                report("Downloading release…", current, total)
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    current += len(chunk)
                    report("Downloading release…", current, total)
                content = b''.join(chunks)

            report("Verifying SHA-256 checksum…")
            expected_sha256: Optional[str] = None
            if release.checksum_url:
                checksum_request = urllib.request.Request(
                    release.checksum_url,
                    headers={"User-Agent": f"htail/{HTAIL_VERSION}"},
                )
                with urllib.request.urlopen(checksum_request, timeout=10.0) as response:
                    checksum_text = response.read().decode("utf-8", errors="replace")
                checksum_match = re.search(r"\b([0-9a-fA-F]{64})\b", checksum_text)
                if not checksum_match:
                    return False, "release checksum asset does not contain a SHA-256 digest"
                expected_sha256 = checksum_match.group(1).lower()

            actual_sha256 = hashlib.sha256(content).hexdigest()
            if expected_sha256 and actual_sha256 != expected_sha256:
                return False, "downloaded update failed SHA-256 verification"

            try:
                source = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                return False, f"downloaded update is not UTF-8 text: {exc}"

            if not source.startswith("#!/"):
                return False, "downloaded update does not look like an executable htail script"
            if f'HTAIL_VERSION = "{release.version}"' not in source:
                return False, (
                    f"downloaded script does not identify itself as version {release.version}"
                )
            try:
                compile(source, str(target), "exec")
            except SyntaxError as exc:
                return False, f"downloaded update failed syntax validation: {exc}"

            report("Preparing update…")
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.update-",
                dir=str(target_dir),
            )
            temp_path = Path(temp_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())

            mode = target.stat().st_mode
            os.chmod(temp_path, mode)

            report("Backing up current executable…")
            backup = target.with_name(target.name + ".bak")
            shutil.copy2(target, backup)
            report("Installing update…")
            os.replace(temp_path, target)
            temp_path = None
            return True, (
                f"updated {target.name} {HTAIL_VERSION} → {release.version}"
                + (" (SHA-256 verified)" if expected_sha256 else "")
            )
        except urllib.error.URLError as exc:
            return False, f"update download failed: {exc.reason}"
        except OSError as exc:
            return False, f"could not install update: {exc}"
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
'''
t = t[:start] + method + t[end:]
p.write_text(t)

# POSIX input: read directly from the fd, not buffered TextIO.
p = Path('src/htail_app/input.py')
t = p.read_text()
old = '''    def _poll_posix(self) -> Optional[InputEvent]:
        try:
            import select

            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if not ready:
                return None
            ch = sys.stdin.read(1)
            if ch != "\x1b":
                if ch == "\t":
                    return "TAB"
                return ch

            seq = ch
            deadline = time.monotonic() + 0.03
            while time.monotonic() < deadline and len(seq) < 48:
                more, _, _ = select.select([sys.stdin], [], [], 0.002)
                if not more:
                    break
                seq += sys.stdin.read(1)
                if seq.startswith("\x1b[<") and seq[-1:] in ("M", "m"):
                    break
                if not seq.startswith("\x1b[<") and (seq.endswith("~") or seq in ("\x1b[A", "\x1b[B", "\x1b[H", "\x1b[F", "\x1bOH", "\x1bOF", "\x1b[Z")):
                    break
            return parse_escape_sequence(seq)
        except Exception:
            return None
'''
new = '''    def _poll_posix(self) -> Optional[InputEvent]:
        try:
            import select

            fd = self._fd if self._fd is not None else sys.stdin.fileno()
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                return None

            def read_char() -> str:
                data = os.read(fd, 1)
                return data.decode("latin1") if data else ""

            ch = read_char()
            if ch != "\x1b":
                if ch == "\t":
                    return "TAB"
                return ch

            seq = ch
            deadline = time.monotonic() + 0.03
            while time.monotonic() < deadline and len(seq) < 48:
                more, _, _ = select.select([fd], [], [], 0.002)
                if not more:
                    break
                seq += read_char()
                if seq.startswith("\x1b[<") and seq[-1:] in ("M", "m"):
                    break
                if not seq.startswith("\x1b[<") and (seq.endswith("~") or seq in ("\x1b[A", "\x1b[B", "\x1b[H", "\x1b[F", "\x1bOH", "\x1bOF", "\x1b[Z")):
                    break
            return parse_escape_sequence(seq)
        except Exception:
            return None
'''
if old not in t:
    raise SystemExit('input.py target not found')
p.write_text(t.replace(old, new))

# Watcher: None means full initial file; -n remains available to noninteractive mode.
p = Path('src/htail_app/watcher.py')
t = p.read_text()
old = '''        try:
            previous, initial_tail = core.read_initial_tail(self.path, self.args.lines, self.args.encoding)
        except OSError as exc:
'''
new = '''        try:
            previous = core.read_lines(self.path, self.args.encoding)
            if self.args.lines is None:
                initial_tail = list(previous)
            elif self.args.lines == 0:
                initial_tail = []
            else:
                initial_tail = list(previous[-self.args.lines:])
        except OSError as exc:
'''
if old not in t:
    raise SystemExit('watcher.py target not found')
p.write_text(t.replace(old, new))

# Pane: first interactive render follows EOF, and titles expose rows above/below.
p = Path('src/htail_app/pane.py')
t = p.read_text()
t = t.replace(
    '        self._pending_anchor_logical: Optional[int] = None\n',
    '        self._pending_anchor_logical: Optional[int] = None\n        self._initial_bottom_pending = False\n',
)
t = t.replace(
'''    def add_initial(self, raw_lines: Sequence[str]) -> None:
        visible = [line for line in raw_lines if self.display_filter.accepts(line)]
        self.lines.extend(core.render_initial_lines(visible, self.highlighter))
        self._mark_layout_dirty()
        self.waiting = False
        self.missing = False
''',
'''    def add_initial(self, raw_lines: Sequence[str]) -> None:
        visible = [line for line in raw_lines if self.display_filter.accepts(line)]
        self.lines.extend(core.render_initial_lines(visible, self.highlighter))
        self._mark_layout_dirty()
        self._initial_bottom_pending = True
        self.waiting = False
        self.missing = False

    def _apply_initial_bottom(self, body_height: int) -> None:
        if not self._initial_bottom_pending:
            return
        self.top = max(0, len(self._visual_lines) - max(0, body_height))
        self._initial_bottom_pending = False
''')
t = t.replace(
'''    def view_rows(self, width: int, height: int) -> List[str]:
        width = max(1, width)
        height = max(0, height)
        self._ensure_layout(width)
        self.top = min(max(0, self.top), max(0, len(self._visual_lines) - 1))
''',
'''    def view_rows(self, width: int, height: int) -> List[str]:
        width = max(1, width)
        height = max(0, height)
        self._ensure_layout(width)
        self._apply_initial_bottom(height)
        self.top = min(max(0, self.top), max(0, len(self._visual_lines) - 1))
''')
t = t.replace(
    '    def title(self, index: int, width: int, focused: bool) -> str:\n',
    '    def title(self, index: int, width: int, focused: bool, body_height: Optional[int] = None) -> str:\n',
)
marker = '''        if self.unseen_updates:
            parts.append(f"+{self.unseen_updates} NEW")
        idle = self.idle_seconds(now)
'''
replacement = '''        if self.unseen_updates:
            parts.append(f"+{self.unseen_updates} NEW")
        if body_height is not None and not self.prefer_snapshot:
            above = min(max(0, self.top), len(self._visual_lines))
            below = max(0, len(self._visual_lines) - (above + max(0, body_height)))
            if above:
                parts.append(f"↑{above}")
            if below:
                parts.append(f"↓{below}")
        idle = self.idle_seconds(now)
'''
if marker not in t:
    raise SystemExit('pane title marker not found')
t = t.replace(marker, replacement)
old = '''        # Resolve pending update anchors before deriving the title/current update.
        self._ensure_layout(inner)
        title = self.title(index, max(1, width - 4), focused)
'''
new = '''        # Resolve initial EOF-following and pending update anchors before deriving
        # the title/current position. Initial view is geometry-based: show the
        # final screenful after wrapping, or the whole file when it fits.
        self._ensure_layout(inner)
        self._apply_initial_bottom(body_h)
        title = self.title(index, max(1, width - 4), focused, body_h)
'''
if old not in t:
    raise SystemExit('pane render marker not found')
p.write_text(t.replace(old, new))

# App: -n no longer caps interactive mode; add CLI + interactive progress.
p = Path('src/htail_app/app.py')
t = p.read_text()
t = t.replace(
    'parser.add_argument("-n", "--lines", type=int, default=50, help="initial lines per file (default: 50)")',
    'parser.add_argument("-n", "--lines", type=int, default=None, help="initial source-line limit for non-interactive output; interactive mode reads the full file")',
)
t = t.replace(
    '    if args.lines < 0:\n        parser.error("--lines must be >= 0")\n',
    '    if args.lines is not None and args.lines < 0:\n        parser.error("--lines must be >= 0")\n',
)
start = t.index('    def _install_worker(self, release: core.ReleaseInfo) -> None:')
end = t.index('\n    def _status_lines', start)
worker = '''    def _install_worker(self, release: core.ReleaseInfo) -> None:
        """Install a confirmed release off the UI thread and schedule restart."""
        target = executable_path()

        def progress(stage: str, current: Optional[int], total: Optional[int]) -> None:
            self.update_install_status = stage
            self.update_install_progress = None if current is None else (current, total)
            self.dirty = True

        try:
            progress("Preparing update…", None, None)
            ok, message = self.update_service.install(release, target, progress=progress)
        except Exception as exc:
            ok, message = False, f"update failed: {exc}"

        self.update_install_result = (ok, message)
        self.update_installing = False
        if ok:
            self.pending_restart = (target, list(sys.argv[1:]), message)
        self.dirty = True
'''
t = t[:start] + worker + t[end:]
helper = '''

class _CLIUpdateProgress:
    """Compact terminal progress reporter used by ``ht --update``."""

    def __init__(self, stream) -> None:
        self.stream = stream
        self.tty = bool(getattr(stream, 'isatty', lambda: False)())
        self.last_stage: Optional[str] = None
        self.open_line = False

    def _newline(self) -> None:
        if self.open_line:
            self.stream.write('\n')
            self.open_line = False

    def __call__(self, stage: str, current: Optional[int], total: Optional[int]) -> None:
        if not self.tty:
            if stage != self.last_stage:
                self.stream.write(f"[htail] {stage}\n")
                self.stream.flush()
            self.last_stage = stage
            return

        if stage != self.last_stage:
            self._newline()
        self.last_stage = stage
        if current is None:
            self.stream.write('\r' + core.CLEAR_LINE + f"[htail] {stage}")
            self.open_line = True
        elif total and total > 0:
            frac = max(0.0, min(1.0, current / total))
            bar_w = 30
            filled = int(round(bar_w * frac))
            bar = '█' * filled + '░' * (bar_w - filled)
            self.stream.write('\r' + core.CLEAR_LINE + f"[htail] [{bar}] {frac * 100:5.1f}%  {current:,}/{total:,} bytes")
            self.open_line = True
        else:
            self.stream.write('\r' + core.CLEAR_LINE + f"[htail] Downloading… {current:,} bytes")
            self.open_line = True
        self.stream.flush()

    def finish(self) -> None:
        self._newline()
        self.stream.flush()
'''
anchor = '\n\ndef run_interactive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter, update_service: core.UpdateService) -> int:\n'
if anchor not in t:
    raise SystemExit('run_interactive anchor not found')
t = t.replace(anchor, helper + anchor)
t = t.replace(
'''def run_interactive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter, update_service: core.UpdateService) -> int:
    app = MultiApp(args, color, display_filter, update_service)
''',
'''def run_interactive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter, update_service: core.UpdateService) -> int:
    # Full-screen panes read the complete current file. The viewport itself
    # decides how much fits after wrapping, so a fixed source-line cap is not
    # meaningful in interactive mode. -n is retained only for compatibility
    # with non-interactive tail-like output.
    args.lines = None
    app = MultiApp(args, color, display_filter, update_service)
''')
t = t.replace(
'''def run_noninteractive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter) -> int:
    panes: List[Pane] = []
''',
'''def run_noninteractive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter) -> int:
    if args.lines is None:
        args.lines = 50
    panes: List[Pane] = []
''')
old = '''        ok, message = update_service.install(release, executable_path())
        print(f"[htail] {message}", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
'''
new = '''        cli_progress = _CLIUpdateProgress(sys.stdout)
        ok, message = update_service.install(release, executable_path(), progress=cli_progress)
        cli_progress.finish()
        print(f"[htail] {message}", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
'''
if old not in t:
    raise SystemExit('CLI update target not found')
p.write_text(t.replace(old, new))

# README semantics.
p = Path('README.md')
t = p.read_text()
t = t.replace(
    '`-n` controls **only the initial context per file**. Once htail is running, every observed change is retained.',
    'Interactive htail reads the **full current file** and initially positions each pane at EOF: if the file fits, the whole file is visible; otherwise the pane shows the final screenful after wrapping. `-n` is retained only for non-interactive tail-like output.',
)
t = t.replace(
    'After confirmation htail downloads the release asset and checksum, verifies SHA-256, keeps a `.bak` copy, atomically replaces itself, and reopens **all watched files with the same command-line options**.',
    'After confirmation htail downloads the release asset with a progress bar, verifies SHA-256, keeps a `.bak` copy, atomically replaces itself, and reopens **all watched files with the same command-line options**. `ht --update` uses the same progress reporting.',
)
t = t.replace('ht -n 0 file.md\n', 'ht -n 20 file.md  # non-interactive initial context override\n')
p.write_text(t)

# Release notes.
Path('RELEASE_NOTES.md').write_text('''# htail 0.8.4

## New features

- Interactive startup is now geometry-based: each pane reads the full file and opens at EOF, showing the whole file when it fits or the final screenful after wrapping when it does not.
- Pane titles show visual rows above/below the viewport (`↑N` / `↓N`) whenever more retained content exists.
- `ht --update` now uses the same staged download progress reporting as the in-app updater.

## Bug fixes

- Fixed POSIX/WSL mouse input by reading escape sequences directly from the terminal file descriptor instead of Python's buffered text stream, which could strand bytes and drop clicks/wheel events.
- Restored byte-level progress updates in the interactive updater while keeping checksum verification, backup, atomic replacement and automatic restart.
- Removed the legacy 50-source-line cap from interactive mode; `-n` remains accepted for non-interactive compatibility only.
''')

# Bundle version assertion.
p = Path('tests/test_bundle.py')
t = p.read_text().replace('htail 0.8.3', 'htail 0.8.4').replace('HTAIL_VERSION = "0.8.3"', 'HTAIL_VERSION = "0.8.4"')
p.write_text(t)
