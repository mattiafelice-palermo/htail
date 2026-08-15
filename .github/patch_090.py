from pathlib import Path


def read(path):
    return Path(path).read_text(encoding='utf-8')

def write(path, text):
    Path(path).write_text(text, encoding='utf-8')

def replace_once(text, old, new, label):
    if old not in text:
        raise SystemExit(f'missing patch target: {label}')
    return text.replace(old, new, 1)

# pane.py
p='src/htail_app/pane.py'
text=read(p)
text=replace_once(text,
'''from dataclasses import dataclass\nfrom pathlib import Path\nimport time\nfrom typing import List, Optional, Sequence, Tuple\n''',
'''from collections import OrderedDict\nfrom dataclasses import dataclass\nfrom pathlib import Path\nimport re\nimport time\nfrom typing import List, Optional, Sequence, Tuple\n''','pane imports')
text=replace_once(text,
'''        idle_warn: float,\n    ) -> None:\n        self.path = path\n''',
'''        idle_warn: float,\n        display_name: Optional[str] = None,\n    ) -> None:\n        self.path = path\n        self.display_name = display_name\n''','pane name init')
text=replace_once(text,
'''        self._snapshot_anchor_pending = False\n\n    @property\n    def name(self) -> str:\n        return self.path.name or str(self.path)\n''',
'''        self._snapshot_anchor_pending = False\n\n        self._wrap_cache: "OrderedDict[Tuple[int, str], Tuple[str, ...]]" = OrderedDict()\n        self._render_cache: "OrderedDict[str, str]" = OrderedDict()\n        self._cache_limit = 12000\n\n    @property\n    def name(self) -> str:\n        return self.display_name or self.path.name or str(self.path)\n\n    def _cache_put(self, cache, key, value) -> None:\n        cache[key] = value\n        cache.move_to_end(key)\n        while len(cache) > self._cache_limit:\n            cache.popitem(last=False)\n\n    def _wrap_cached(self, text: str, width: int) -> List[str]:\n        key = (max(1, width), text)\n        cached = self._wrap_cache.get(key)\n        if cached is not None:\n            self._wrap_cache.move_to_end(key)\n            return list(cached)\n        wrapped = tuple(core.wrap_ansi(text, max(1, width)) or [""])\n        self._cache_put(self._wrap_cache, key, wrapped)\n        return list(wrapped)\n\n    def _render_snapshot_lines(self, raw_visible: Sequence[str]) -> List[str]:\n        if not self.highlighter.enabled:\n            return [line.rstrip("\\r\\n") for line in raw_visible]\n        if self.highlighter.mode == "markdown-rendered":\n            fence_re = re.compile(r"^\\s*(?:```|~~~)")\n            if not any(fence_re.match(line.rstrip("\\r\\n")) for line in raw_visible):\n                rendered: List[str] = []\n                for raw in raw_visible:\n                    body = raw.rstrip("\\r\\n")\n                    cached = self._render_cache.get(body)\n                    if cached is None:\n                        cached = self.highlighter._render_markdown_line(body)\n                        self._cache_put(self._render_cache, body, cached)\n                    else:\n                        self._render_cache.move_to_end(body)\n                    rendered.append(cached)\n                return rendered\n        return self.highlighter.render_lines(raw_visible)\n''','pane cache helpers')
text=text.replace('wrapped = core.wrap_ansi(line, width) or [""]','wrapped = self._wrap_cached(line, width)')
text=replace_once(text,
'''        raw_visible = [line for _, line in indexed]\n        if self.highlighter.enabled:\n            styled = self.highlighter.render_lines(raw_visible)\n        else:\n            styled = [line.rstrip("\\r\\n") for line in raw_visible]\n''',
'''        raw_visible = [line for _, line in indexed]\n        styled = self._render_snapshot_lines(raw_visible)\n''','snapshot render cache')
text=text.replace('visual.extend(core.wrap_ansi(self.snapshot_update_header, width) or [""])','visual.extend(self._wrap_cached(self.snapshot_update_header, width))')
text=text.replace('visual.extend(core.wrap_ansi(row, width) or [""])','visual.extend(self._wrap_cached(row, width))')
write(p,text)

# app.py
p='src/htail_app/app.py'
text=read(p)
text=replace_once(text,
'''from .pane import Pane, StreamPane\nfrom .watcher import FileFollower, WatchNotice, WatchUpdate\n''',
'''from .pane import Pane, StreamPane\nfrom .sources import CommandFollower, StreamFollower\nfrom .watcher import FileFollower, WatchNotice, WatchUpdate\n''','source imports')
text=replace_once(text,
'''    parser.add_argument("files", type=Path, nargs="*", help="text files to watch")\n''',
'''    parser.add_argument("files", type=Path, nargs="*", help="text files to watch; use '-' for stdin")\n    parser.add_argument("--exec", dest="commands", action="append", default=[], metavar="COMMAND", help="run a shell command and watch its merged stdout/stderr; repeatable")\n    parser.add_argument("--pid", type=int, metavar="PID", help="exit after this process is no longer running")\n''','parser sources')
text=replace_once(text,
'''    if args.idle_warn < 0:\n        parser.error("--idle-warn must be >= 0")\n    return args\n''',
'''    if args.idle_warn < 0:\n        parser.error("--idle-warn must be >= 0")\n    if args.pid is not None and args.pid <= 0:\n        parser.error("--pid must be > 0")\n    if sum(1 for path in args.files if str(path) == "-") > 1:\n        parser.error("stdin ('-') can only be used once")\n    return args\n''','parser validation')
text=replace_once(text,'\ndef _install_executable(command_name: str) -> Tuple[bool, str, Path]:\n', '''\n\ndef _process_alive(pid: int) -> bool:\n    if pid <= 0:\n        return False\n    if os.name != "nt":\n        try:\n            os.kill(pid, 0)\n        except ProcessLookupError:\n            return False\n        except PermissionError:\n            return True\n        except OSError:\n            return False\n        return True\n    try:\n        import ctypes\n        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000\n        STILL_ACTIVE = 259\n        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)\n        if not handle:\n            return False\n        code = ctypes.c_ulong()\n        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))\n        ctypes.windll.kernel32.CloseHandle(handle)\n        return bool(ok and code.value == STILL_ACTIVE)\n    except Exception:\n        return True\n\n\ndef _install_executable(command_name: str) -> Tuple[bool, str, Path]:\n''','process alive')

start=text.index('        self.paths = list(args.files)\n')
end=text.index('    def _stream_initial(',start)
new='''        self.paths = list(args.files)\n        self.followers: List[object] = []\n        self.panes: List[Pane] = []\n        self.stream = StreamPane(color, args.idle_warn)\n        self.layout = args.layout\n        self.focus = 0\n        self.maximized = False\n        self.layout_menu = False\n        self.help_active = False\n        self.update_confirm_active = False\n        self.update_installing = False\n        self.update_install_status = ""\n        self.update_install_progress: Optional[Tuple[int, Optional[int]]] = None\n        self.update_overall_progress = 0.0\n        self.update_progress_started_at: Optional[float] = None\n        self.update_install_result: Optional[Tuple[bool, str]] = None\n        self.update_release: Optional[core.ReleaseInfo] = None\n        self.pending_restart: Optional[Tuple[Path, List[str], str]] = None\n        self.pending_restart_at: Optional[float] = None\n        self.update_check_done = False\n        self.update_check_error: Optional[str] = None\n        self.update_manual_check_pending = False\n        self.last_update_check_monotonic = time.monotonic()\n        self.message: Optional[str] = None\n        self.message_until = 0.0\n        self.last_status_second: Optional[int] = None\n        self.last_rects: List[Tuple[int, Rect]] = []\n        self.dirty = True\n\n        for path in self.paths:\n            if str(path) == "-":\n                pseudo = Path("stdin.txt")\n                highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)\n                pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name="stdin")\n                follower = StreamFollower(sys.stdin, args, label="stdin")\n            else:\n                highlighter = core.SyntaxHighlighter(path, args.syntax, color)\n                pane = Pane(path, highlighter, display_filter, color, args.idle_warn)\n                follower = FileFollower(path, args)\n            notice = follower.initialize_if_available()\n            if notice and notice.initial_tail is not None:\n                pane.add_initial(notice.initial_tail)\n                pane.set_snapshot(follower.previous)\n                if notice.initial_tail:\n                    self._stream_initial(len(self.panes), pane, notice.initial_tail)\n            else:\n                pane.waiting = True\n                if notice and notice.kind == "error":\n                    pane.add_system_line(notice.text, warning=True)\n            if highlighter.warning:\n                pane.add_system_line(highlighter.warning, warning=True)\n            self.panes.append(pane)\n            self.followers.append(follower)\n\n        for command_index, command in enumerate(args.commands, start=1):\n            pseudo = Path(f"command-{command_index}.log")\n            highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)\n            label = f"$ {command}"\n            pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name=label)\n            follower = CommandFollower(command, args, label=label)\n            follower.initialize_if_available()\n            pane.set_message(f"running pid {follower.process.pid}", 4.0)\n            self.panes.append(pane)\n            self.followers.append(follower)\n\n'''
text=text[:start]+new+text[end:]
text=text.replace('f"After updating, htail will reopen all {len(self.paths)} watched file{\'s\' if len(self.paths) != 1 else \'\'}."','f"After updating, htail will reopen all {len(self.panes)} source{\'s\' if len(self.panes) != 1 else \'\'}."')
text=replace_once(text,
'''    def __exit__(self, exc_type, exc, tb) -> None:\n        sys.stdout.write(core.SHOW_CURSOR + core.RESET + core.ALT_SCREEN_OFF)\n        sys.stdout.flush()\n''',
'''    def __exit__(self, exc_type, exc, tb) -> None:\n        for follower in self.followers:\n            close = getattr(follower, "close", None)\n            if close is not None:\n                try:\n                    close()\n                except Exception:\n                    pass\n        sys.stdout.write(core.SHOW_CURSOR + core.RESET + core.ALT_SCREEN_OFF)\n        sys.stdout.flush()\n''','close sources')
text=replace_once(text,
'''            elif key in ("y", "Y") and self.update_release is not None and not self.update_installing:\n                self.update_installing = True\n''',
'''            elif key in ("y", "Y") and self.update_release is not None and not self.update_installing:\n                if self.args.commands:\n                    self.update_confirm_active = False\n                    self.set_message("update not installed during --exec; run 'ht --update' separately", 6.0)\n                    return False\n                self.update_installing = True\n''','exec update safety')
text=replace_once(text,
'''                elif result.kind == "error":\n                    pane.add_system_line(result.text, warning=True)\n                self.dirty = True\n                continue\n''',
'''                elif result.kind == "ended":\n                    pane.waiting = False\n                    pane.missing = False\n                    pane.set_message(result.text, 6.0)\n                elif result.kind == "error":\n                    pane.add_system_line(result.text, warning=True)\n                self.dirty = True\n                continue\n''','ended source')
text=replace_once(text,
'''    next_watch_poll = 0.0\n    try:\n''',
'''    next_watch_poll = 0.0\n    next_pid_check = 0.0\n    try:\n''','pid loop state')
text=replace_once(text,
'''                now = time.monotonic()\n                if now >= next_watch_poll:\n''',
'''                now = time.monotonic()\n                if args.pid is not None and now >= next_pid_check:\n                    next_pid_check = now + 0.25\n                    if not _process_alive(args.pid):\n                        app.set_message(f"pid {args.pid} exited", 1.0)\n                        app.render()\n                        break\n                if now >= next_watch_poll:\n''','pid interactive')

start=text.index('def run_noninteractive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter) -> int:\n')
end=text.index('\n\ndef main(',start)
new_non=r'''def run_noninteractive(args: argparse.Namespace, color: bool, display_filter: core.DisplayFilter) -> int:
    if args.lines is None:
        args.lines = 50
    panes: List[Pane] = []
    followers: List[object] = []
    for path in args.files:
        if str(path) == "-":
            pseudo = Path("stdin.txt")
            highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)
            pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name="stdin")
            follower = StreamFollower(sys.stdin, args, label="stdin")
        else:
            highlighter = core.SyntaxHighlighter(path, args.syntax, color)
            pane = Pane(path, highlighter, display_filter, color, args.idle_warn)
            follower = FileFollower(path, args)
        notice = follower.initialize_if_available()
        panes.append(pane); followers.append(follower)
        if notice and notice.initial_tail is not None:
            if not args.no_start_banner:
                print(f"[htail {VERSION}] [{len(panes)}] watching {pane.name} · syntax: {highlighter.syntax_name}")
            visible = [line for line in notice.initial_tail if display_filter.accepts(line)]
            for line in core.render_initial_lines(visible, highlighter):
                print(line)
        elif not args.no_start_banner:
            print(f"[htail] [{len(panes)}] waiting for {pane.name}", file=sys.stderr)
    for command_index, command in enumerate(args.commands, start=1):
        pseudo = Path(f"command-{command_index}.log")
        highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)
        pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name=f"$ {command}")
        follower = CommandFollower(command, args, label=pane.name)
        follower.initialize_if_available()
        panes.append(pane); followers.append(follower)
        if not args.no_start_banner:
            print(f"[htail {VERSION}] [{len(panes)}] running {command} (pid {follower.process.pid})")
    try:
        while True:
            time.sleep(args.interval)
            now = time.monotonic()
            if args.pid is not None and not _process_alive(args.pid):
                return 0
            for index, follower in enumerate(followers):
                result = follower.poll(now)
                if isinstance(result, WatchUpdate):
                    _render_stream_event(index, panes[index], result, args, display_filter, color)
                elif isinstance(result, WatchNotice) and result.kind == "error":
                    print(f"[htail] [{index + 1}] {result.text}", file=sys.stderr)
            if followers and all(bool(getattr(follower, "finished", False)) for follower in followers):
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        for follower in followers:
            close = getattr(follower, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
'''
text=text[:start]+new_non+text[end:]
text=replace_once(text,
'''    if not args.files:\n        print(f"htail {VERSION}")\n        print("Usage: ht FILE [FILE ...]")\n        print("Example: ht reviewer.md implementer.md")\n        return 0\n''',
'''    if not args.files and not args.commands and not sys.stdin.isatty():\n        args.files = [Path("-")]\n\n    if not args.files and not args.commands:\n        print(f"htail {VERSION}")\n        print("Usage: ht FILE [FILE ...] | producer | ht | ht --exec COMMAND")\n        print("Example: ht reviewer.md implementer.md")\n        return 0\n''','auto stdin')
text=replace_once(text,
'''    interactive = sys.stdin.isatty() and sys.stdout.isatty()\n    if interactive:\n''',
'''    has_stdin_source = any(str(path) == "-" for path in args.files)\n    interactive = sys.stdout.isatty() and (sys.stdin.isatty() or has_stdin_source or bool(args.commands))\n    if interactive:\n''','piped interactive')
write(p,text)

# README
p='README.md'
text=read(p)
text=replace_once(text,
'''Watch several files at once:\n\n```bash\nht reviewer.md implementer.md\nht --layout rows reviewer.md implementer.md\nht --layout columns reviewer.md implementer.md\nht --layout grid *.log\nht --layout stream reviewer.md implementer.md\n```\n''',
'''Watch several files at once:\n\n```bash\nht reviewer.md implementer.md\nht --layout rows reviewer.md implementer.md\nht --layout columns reviewer.md implementer.md\nht --layout grid *.log\nht --layout stream reviewer.md implementer.md\n```\n\nWatch a pipe or run a command directly:\n\n```bash\npytest -q | ht\ncat build.log | ht -\nht --exec "pytest -vv"\nht server.log --exec "python worker.py"\nht --pid 12345 server.log\n```\n\nWhen stdin is a pipe, htail reads keyboard/mouse controls from the controlling terminal, so the full-screen UI remains interactive. `--exec` is repeatable and merges the child command's stderr into its stdout pane.\n''','readme sources')
text=replace_once(text,
'''- Robust following across append, truncation, rewrite, atomic replacement, staged writes, and same-size rewrites.\n''',
'''- Robust following across append, truncation, rewrite, atomic replacement, staged writes, and same-size rewrites.\n- Fast append-only path: ordinary growing logs are consumed from the previous byte offset instead of rereading the complete file on every append.\n- Incremental Markdown render/wrap caches reuse unchanged visual work across small updates.\n''','readme perf')
text += '''\n## Benchmarks\n\nA synthetic benchmark harness is available for before/after-style comparisons on the same machine:\n\n```bash\nPYTHONPATH=src python benchmarks/benchmark_htail.py\nPYTHONPATH=src python benchmarks/benchmark_htail.py --sizes 1 10 50 100 --iterations 3\n```\n\nAbsolute timings depend on storage and VM hardware; the ratios are the useful metric. See `docs/NEXT.md` for deliberately deferred ideas.\n'''
write(p,text)
print('ui/app/readme patch applied')
