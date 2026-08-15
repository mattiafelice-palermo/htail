from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# app.py: deduplicate overlapping initial globs and support dynamic globs in
# non-interactive mode too.
# ---------------------------------------------------------------------------
path = "src/htail_app/app.py"
text = read(path)
text = replace_once(
    text,
    '''        for tracker in self.glob_trackers:\n            self.native_watch.add_directory(tracker.root)\n            self.paths.extend(tracker.scan())\n\n        self.followers: List[object] = []\n''',
    '''        for tracker in self.glob_trackers:\n            self.native_watch.add_directory(tracker.root)\n            self.paths.extend(tracker.scan())\n\n        deduped_paths: List[Path] = []\n        initial_seen = set()\n        for path in self.paths:\n            key = ("stdin",) if str(path) == "-" else ("file", os.path.abspath(os.fspath(path)))\n            if key in initial_seen:\n                continue\n            initial_seen.add(key)\n            deduped_paths.append(path)\n        self.paths = deduped_paths\n\n        self.followers: List[object] = []\n''',
    "dedupe initial glob paths",
)

start = text.index("def run_noninteractive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter) -> int:\n")
end = text.index("\n\ndef main(argv: Optional[Sequence[str]] = None) -> int:\n", start)
new_noninteractive = '''def run_noninteractive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter) -> int:\n    if args.lines is None:\n        args.lines = 50\n\n    glob_trackers = [\n        DynamicGlob(str(path))\n        for path in args.files\n        if str(path) != "-" and has_magic(str(path))\n    ]\n    glob_trackers.extend(DynamicGlob(pattern) for pattern in args.globs)\n    initial_paths = [\n        path for path in args.files\n        if str(path) == "-" or not has_magic(str(path))\n    ]\n    for tracker in glob_trackers:\n        initial_paths.extend(tracker.scan())\n\n    panes: List[Pane] = []\n    followers: List[object] = []\n    known_paths = set()\n\n    def add_file_source(path: Path) -> bool:\n        if str(path) == "-":\n            key = ("stdin",)\n        else:\n            key = ("file", os.path.abspath(os.fspath(path)))\n        if key in known_paths:\n            return False\n        known_paths.add(key)\n\n        if str(path) == "-":\n            pseudo = Path("stdin.txt")\n            highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)\n            pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name="stdin")\n            follower = StreamFollower(sys.stdin, args, label="stdin")\n        else:\n            highlighter = core.SyntaxHighlighter(path, args.syntax, color)\n            pane = Pane(path, highlighter, display_filter, color, args.idle_warn)\n            follower = FileFollower(path, args)\n        notice = follower.initialize_if_available()\n        panes.append(pane)\n        followers.append(follower)\n        if notice and notice.initial_tail is not None:\n            if not args.no_start_banner:\n                print(f"[htail {VERSION}] [{len(panes)}] watching {pane.name} · syntax: {highlighter.syntax_name}")\n            visible = [line for line in notice.initial_tail if display_filter.accepts(line)]\n            for line in core.render_initial_lines(visible, highlighter):\n                print(line)\n        elif not args.no_start_banner:\n            print(f"[htail] [{len(panes)}] waiting for {pane.name}", file=sys.stderr)\n        return True\n\n    for path in initial_paths:\n        add_file_source(path)\n\n    for command_index, command in enumerate(args.commands, start=1):\n        pseudo = Path(f"command-{command_index}.log")\n        highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)\n        pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name=f"$ {command}")\n        follower = CommandFollower(command, args, label=pane.name)\n        follower.initialize_if_available()\n        panes.append(pane)\n        followers.append(follower)\n        if not args.no_start_banner:\n            print(f"[htail {VERSION}] [{len(panes)}] running {command} (pid {follower.process.pid})")\n\n    next_glob_scan = 0.0\n    try:\n        while True:\n            time.sleep(args.interval)\n            now = time.monotonic()\n            if args.pid is not None and not _process_alive(args.pid):\n                return 0\n\n            if glob_trackers and now >= next_glob_scan:\n                next_glob_scan = now + 2.0\n                for tracker in glob_trackers:\n                    for path in tracker.scan():\n                        add_file_source(path)\n\n            for index, follower in list(enumerate(followers)):\n                result = follower.poll(now)\n                if isinstance(result, WatchUpdate):\n                    _render_stream_event(index, panes[index], result, args, display_filter, color)\n                elif isinstance(result, WatchNotice) and result.kind == "error":\n                    print(f"[htail] [{index + 1}] {result.text}", file=sys.stderr)\n            if followers and not glob_trackers and all(bool(getattr(follower, "finished", False)) for follower in followers):\n                return 0\n    except KeyboardInterrupt:\n        return 0\n    finally:\n        for follower in followers:\n            close = getattr(follower, "close", None)\n            if close is not None:\n                try:\n                    close()\n                except Exception:\n                    pass\n'''
text = text[:start] + new_noninteractive + text[end:]
write(path, text)


# ---------------------------------------------------------------------------
# reference_probe.py: compare the final visible terminal screen, not the raw
# optimized cursor command stream.
# ---------------------------------------------------------------------------
path = "benchmarks/reference_probe.py"
text = read(path)
text = replace_once(
    text,
    "import tempfile\nimport time\n",
    "import tempfile\nimport time\nimport re\n",
    "reference regex import",
)
insert_before = "\ndef main() -> int:\n"
emulator = r'''

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
'''
if insert_before not in text:
    raise RuntimeError("reference probe main marker missing")
text = text.replace(insert_before, emulator + insert_before, 1)
old = '''        capture = Capture()\n        old_stdout = sys.stdout\n        try:\n            sys.stdout = capture\n            application.render()\n            capture.seek(0)\n            capture.truncate(0)\n            application.set_message("reference-status-change", 10.0)\n            start = time.perf_counter()\n            application.render()\n            elapsed = time.perf_counter() - start\n            output = capture.getvalue().encode("utf-8")\n        finally:\n            sys.stdout = old_stdout\n            close = getattr(application, "close_native_watch", None)\n            if close is not None:\n                close()\n        performance["status_redraw_ms"] = elapsed * 1000.0\n        performance["status_redraw_bytes"] = len(output)\n'''
new = '''        capture = Capture()\n        old_stdout = sys.stdout\n        try:\n            sys.stdout = capture\n            application.render()\n            first_end = capture.tell()\n            application.set_message("reference-status-change", 10.0)\n            start = time.perf_counter()\n            application.render()\n            elapsed = time.perf_counter() - start\n            combined_output = capture.getvalue()\n            incremental_output = combined_output[first_end:].encode("utf-8")\n        finally:\n            sys.stdout = old_stdout\n            close = getattr(application, "close_native_watch", None)\n            if close is not None:\n                close()\n        behavior["final_terminal_frame"] = emulate_terminal(combined_output, 120, 40)\n        performance["status_redraw_ms"] = elapsed * 1000.0\n        performance["status_redraw_bytes"] = len(incremental_output)\n'''
text = replace_once(text, old, new, "reference terminal frame capture")
write(path, text)


# ---------------------------------------------------------------------------
# Regression coverage for non-interactive glob discovery and overlapping glob
# deduplication.
# ---------------------------------------------------------------------------
path = "tests/test_regressions_010.py"
text = read(path)
text = replace_once(
    text,
    "from pathlib import Path\nimport os\n",
    "from pathlib import Path\nimport io\nimport os\n",
    "test io import",
)
marker = "\n\nclass RegexInteractionTests(unittest.TestCase):\n"
addition = '''\n    def test_overlapping_initial_globs_do_not_duplicate_panes(self):\n        with tempfile.TemporaryDirectory() as td:\n            root = Path(td)\n            target = root / "one.log"\n            target.write_text("x\\n", encoding="utf-8")\n            args = app.parse_args([\n                "--glob", str(root / "*.log"),\n                "--glob", str(root / "one.*"),\n                "--no-native-watch", "--no-color",\n            ])\n            application = MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))\n            try:\n                self.assertEqual([pane.name for pane in application.panes], ["one.log"])\n            finally:\n                application.close_native_watch()\n\n    def test_noninteractive_glob_includes_existing_match(self):\n        with tempfile.TemporaryDirectory() as td:\n            root = Path(td)\n            target = root / "one.log"\n            target.write_text("hello from glob\\n", encoding="utf-8")\n            args = app.parse_args(["--glob", str(root / "*.log"), "--no-start-banner", "--no-color"])\n            # Avoid entering the follow loop: a pid known to be absent exits at\n            # its first iteration, after initial glob content has been printed.\n            args.pid = 999999999\n            out = io.StringIO()\n            old_stdout = app.sys.stdout\n            try:\n                app.sys.stdout = out\n                app.run_noninteractive(args, False, core.DisplayFilter())\n            finally:\n                app.sys.stdout = old_stdout\n            self.assertIn("hello from glob", out.getvalue())\n'''
if marker not in text:
    raise RuntimeError("glob test insertion marker missing")
text = text.replace(marker, addition + marker, 1)
write(path, text)

print("0.10.0 final compatibility refinements applied")
