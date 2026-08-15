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
# watcher.py: native notifications gate expensive idle metadata probes only
# when the application has an actual native backend.
# ---------------------------------------------------------------------------
path = "src/htail_app/watcher.py"
text = read(path)
text = replace_once(
    text,
    "        self._notification_hint = True\n        self.fast_append_hits = 0\n",
    "        self._notification_hint = True\n        self.notification_gated = bool(getattr(args, 'notification_gated', False))\n        self.fast_append_hits = 0\n",
    "watcher notification mode",
)
text = replace_once(
    text,
    "        if not self._notification_hint and not self.has_pending_change and not verify_due:\n            return None\n",
    "        if self.notification_gated and not self._notification_hint and not self.has_pending_change and not verify_due:\n            return None\n",
    "watcher gated poll",
)
write(path, text)


# ---------------------------------------------------------------------------
# pane.py: interactive regex search and persistent regex highlighting.
# ---------------------------------------------------------------------------
path = "src/htail_app/pane.py"
text = read(path)
text = replace_once(
    text,
    "from typing import List, Optional, Sequence, Tuple\n",
    "from typing import Dict, List, Optional, Pattern, Sequence, Tuple\n",
    "pane typing",
)
text = replace_once(
    text,
    "def _pad_ansi(text: str, width: int) -> str:\n    text = core.clip_ansi(text, max(0, width))\n    visible = len(core.strip_ansi(text))\n    if visible < width:\n        text += \" \" * (width - visible)\n    return text\n\n\nclass Pane:\n",
    '''def _pad_ansi(text: str, width: int) -> str:\n    text = core.clip_ansi(text, max(0, width))\n    visible = len(core.strip_ansi(text))\n    if visible < width:\n        text += " " * (width - visible)\n    return text\n\n\ndef _inject_regex_style(text: str, pattern: Optional[Pattern[str]], on: str, off: str) -> str:\n    """Apply an SGR attribute to visible regex spans without destroying syntax ANSI."""\n    if pattern is None:\n        return text\n    plain = core.strip_ansi(text)\n    spans = [(m.start(), m.end()) for m in pattern.finditer(plain) if m.end() > m.start()]\n    if not spans:\n        return text\n\n    boundaries: List[int] = [0] * (len(plain) + 1)\n    raw = visible = 0\n    while raw < len(text) and visible < len(plain):\n        match = core.ANSI_RE.match(text, raw)\n        if match:\n            raw = match.end()\n            continue\n        boundaries[visible] = raw\n        visible += 1\n        raw += 1\n    boundaries[visible] = raw\n\n    for start, end in reversed(spans):\n        raw_start = boundaries[start]\n        raw_end = boundaries[end]\n        text = text[:raw_end] + off + text[raw_end:]\n        text = text[:raw_start] + on + text[raw_start:]\n    return text\n\n\nclass Pane:\n''',
    "pane regex helper",
)
text = replace_once(
    text,
    "        self._snapshot_visual_lines: List[str] = []\n        self._snapshot_top = 0\n        self._snapshot_anchor_pending = False\n\n        self._wrap_cache:",
    "        self._snapshot_visual_lines: List[str] = []\n        self._snapshot_source_to_visual: Dict[int, int] = {}\n        self._snapshot_visual_to_source: List[Optional[int]] = []\n        self._snapshot_top = 0\n        self._snapshot_anchor_pending = False\n\n        self.search_pattern = \"\"\n        self.search_regex: Optional[Pattern[str]] = None\n        self.highlight_pattern = \"\"\n        self.highlight_regex: Optional[Pattern[str]] = None\n\n        self._wrap_cache:",
    "pane search state",
)
text = replace_once(
    text,
    "    def set_message(self, text: str, duration: float = 2.5) -> None:\n",
    '''    def _apply_regex_marks(self, row: str) -> str:\n        if not self.color:\n            return row\n        # Underline is the persistent user highlight; reverse video is the\n        # current search expression. Attribute-specific off codes preserve the\n        # foreground/bold syntax styles already present in the row.\n        row = _inject_regex_style(row, self.highlight_regex, "\\x1b[4m", "\\x1b[24m")\n        row = _inject_regex_style(row, self.search_regex, "\\x1b[7m", "\\x1b[27m")\n        return row\n\n    def set_search(self, expression: str, flags: int = 0) -> Optional[str]:\n        if not expression:\n            self.search_pattern = ""\n            self.search_regex = None\n            self._mark_layout_dirty()\n            self._snapshot_layout_dirty = True\n            return None\n        try:\n            compiled = re.compile(expression, flags)\n        except re.error as exc:\n            return str(exc)\n        self.search_pattern = expression\n        self.search_regex = compiled\n        self._mark_layout_dirty()\n        self._snapshot_layout_dirty = True\n        return None\n\n    def set_highlight(self, expression: str, flags: int = 0) -> Optional[str]:\n        if not expression:\n            self.clear_highlight()\n            return None\n        try:\n            compiled = re.compile(expression, flags)\n        except re.error as exc:\n            return str(exc)\n        self.highlight_pattern = expression\n        self.highlight_regex = compiled\n        self._mark_layout_dirty()\n        self._snapshot_layout_dirty = True\n        return None\n\n    def clear_highlight(self) -> None:\n        self.highlight_pattern = ""\n        self.highlight_regex = None\n        self._mark_layout_dirty()\n        self._snapshot_layout_dirty = True\n        self.set_message("regex highlight cleared")\n\n    def search_next(self, reverse: bool, width: int, body_height: int) -> bool:\n        pattern = self.search_regex\n        if pattern is None:\n            self.set_message("no active search")\n            return False\n\n        width = max(1, width)\n        body_height = max(1, body_height)\n        if self.snapshot_raw:\n            self.prefer_snapshot = True\n            self._ensure_snapshot_layout(width)\n            candidates = [\n                i for i, line in enumerate(self.snapshot_raw)\n                if self.display_filter.accepts(line)\n                and pattern.search(line.rstrip("\\r\\n")) is not None\n                and i in self._snapshot_source_to_visual\n            ]\n            if not candidates:\n                self.set_message(f"no match: /{self.search_pattern}/")\n                return False\n            current_source = -1\n            if self._snapshot_visual_to_source:\n                start = min(max(0, self._snapshot_top), len(self._snapshot_visual_to_source) - 1)\n                for source in self._snapshot_visual_to_source[start:]:\n                    if source is not None:\n                        current_source = source\n                        break\n            if reverse:\n                prior = [i for i in candidates if i < current_source]\n                target = prior[-1] if prior else candidates[-1]\n            else:\n                later = [i for i in candidates if i > current_source]\n                target = later[0] if later else candidates[0]\n            self._snapshot_top = min(\n                self._snapshot_source_to_visual[target],\n                self._snapshot_max_top(body_height),\n            )\n            position = candidates.index(target) + 1\n            self.set_message(f"match {position}/{len(candidates)}: /{self.search_pattern}/")\n            return True\n\n        self._ensure_layout(width)\n        candidates = [\n            i for i, line in enumerate(self.lines)\n            if pattern.search(core.strip_ansi(line)) is not None\n        ]\n        if not candidates:\n            self.set_message(f"no match: /{self.search_pattern}/")\n            return False\n        current = self._logical_at_top()\n        if reverse:\n            prior = [i for i in candidates if i < current]\n            target = prior[-1] if prior else candidates[-1]\n        else:\n            later = [i for i in candidates if i > current]\n            target = later[0] if later else candidates[0]\n        self.top = min(self._logical_to_visual[target], self._max_top(body_height))\n        position = candidates.index(target) + 1\n        self.set_message(f"match {position}/{len(candidates)}: /{self.search_pattern}/")\n        return True\n\n    def set_message(self, text: str, duration: float = 2.5) -> None:\n''',
    "pane search methods",
)
text = replace_once(
    text,
    "            wrapped = self._wrap_cached(line, width)\n",
    "            wrapped = self._wrap_cached(self._apply_regex_marks(line), width)\n",
    "pane history regex marks",
)
text = replace_once(
    text,
    "        self._snapshot_layout_width = width\n        self._snapshot_layout_dirty = False\n        indexed = [\n",
    "        self._snapshot_layout_width = width\n        self._snapshot_layout_dirty = False\n        self._snapshot_source_to_visual = {}\n        self._snapshot_visual_to_source = []\n        indexed = [\n",
    "pane snapshot mappings reset",
)
text = replace_once(
    text,
    "                visual.extend(self._wrap_cached(self.snapshot_update_header, width))\n                header_inserted = True\n            if changed:\n                row = core.paint(\"▌ \", core.BOLD_LIGHT_CYAN, self.color) + row\n            visual.extend(self._wrap_cached(row, width))\n\n        if self.snapshot_update_header and not header_inserted:\n            anchor = len(visual)\n            visual.extend(self._wrap_cached(self.snapshot_update_header, width))\n",
    '''                header_rows = self._wrap_cached(self.snapshot_update_header, width)\n                visual.extend(header_rows)\n                self._snapshot_visual_to_source.extend([None] * len(header_rows))\n                header_inserted = True\n            if changed:\n                row = core.paint("▌ ", core.BOLD_LIGHT_CYAN, self.color) + row\n            row = self._apply_regex_marks(row)\n            self._snapshot_source_to_visual[source_index] = len(visual)\n            wrapped_rows = self._wrap_cached(row, width)\n            visual.extend(wrapped_rows)\n            self._snapshot_visual_to_source.extend([source_index] * len(wrapped_rows))\n\n        if self.snapshot_update_header and not header_inserted:\n            anchor = len(visual)\n            header_rows = self._wrap_cached(self.snapshot_update_header, width)\n            visual.extend(header_rows)\n            self._snapshot_visual_to_source.extend([None] * len(header_rows))\n''',
    "pane snapshot regex mappings",
)
write(path, text)


# ---------------------------------------------------------------------------
# app.py: glob discovery, native notifications, damage rendering, prompt UI,
# one-key update flow.
# ---------------------------------------------------------------------------
path = "src/htail_app/app.py"
text = read(path)
text = replace_once(
    text,
    "from .input import InputReader, InputEvent, MouseEvent\n",
    "from .input import InputReader, InputEvent, MouseEvent\nfrom .fsnotify import FsEvents, NativeWatchHub\nfrom .globwatch import DynamicGlob, has_magic\n",
    "app imports",
)
text = replace_once(
    text,
    "    parser.add_argument(\"files\", type=Path, nargs=\"*\", help=\"text files to watch; use '-' for stdin\")\n",
    "    parser.add_argument(\"files\", type=Path, nargs=\"*\", help=\"text files or quoted glob patterns to watch; use '-' for stdin\")\n    parser.add_argument(\"--glob\", dest=\"globs\", action=\"append\", default=[], metavar=\"PATTERN\", help=\"dynamically add files matching PATTERN; repeatable\")\n",
    "app glob parser",
)
text = replace_once(
    text,
    "    parser.add_argument(\n        \"--no-mouse\",\n        action=\"store_true\",\n        help=\"disable terminal mouse tracking (keyboard pane selection still works)\",\n    )\n",
    "    parser.add_argument(\n        \"--no-mouse\",\n        action=\"store_true\",\n        help=\"disable terminal mouse tracking (keyboard pane selection still works)\",\n    )\n    parser.add_argument(\"--no-native-watch\", action=\"store_true\", help=\"disable native filesystem notifications and use polling only\")\n",
    "app native parser",
)
text = replace_once(
    text,
    "def _overlay_modal(background: Sequence[str], panel: Sequence[str], width: int, height: int, color: bool) -> List[str]:\n",
    '''def _changed_frame_rows(previous: Optional[Sequence[str]], current: Sequence[str]) -> List[int]:\n    if previous is None or len(previous) != len(current):\n        return list(range(len(current)))\n    return [i for i, (old, new) in enumerate(zip(previous, current)) if old != new]\n\n\ndef _overlay_modal(background: Sequence[str], panel: Sequence[str], width: int, height: int, color: bool) -> List[str]:\n''',
    "damage helper",
)
text = replace_once(
    text,
    "        self.paths = list(args.files)\n        self.followers: List[object] = []\n",
    '''        raw_paths = list(args.files)\n        self.glob_trackers = [\n            DynamicGlob(str(path))\n            for path in raw_paths\n            if str(path) != "-" and has_magic(str(path))\n        ]\n        self.glob_trackers.extend(DynamicGlob(pattern) for pattern in args.globs)\n        self.paths = [\n            path for path in raw_paths\n            if str(path) == "-" or not has_magic(str(path))\n        ]\n        self.native_watch = NativeWatchHub(enabled=not args.no_native_watch)\n        setattr(self.args, "notification_gated", self.native_watch.available)\n        self._known_file_paths = set()\n        self._next_glob_scan = 0.0\n        for tracker in self.glob_trackers:\n            self.native_watch.add_directory(tracker.root)\n            self.paths.extend(tracker.scan())\n\n        self.followers: List[object] = []\n''',
    "app source setup",
)
text = replace_once(
    text,
    "        self.last_rects: List[Tuple[int, Rect]] = []\n        self.dirty = True\n\n        for path in self.paths:\n",
    '''        self.last_rects: List[Tuple[int, Rect]] = []\n        self.dirty = True\n        self.prompt_mode: Optional[str] = None\n        self.prompt_buffer = ""\n        self._last_frame: Optional[List[str]] = None\n        self._last_frame_geometry: Optional[Tuple[int, int]] = None\n        self.render_rows_written = 0\n        self.render_frames = 0\n\n        for path in self.paths:\n''',
    "app ui state",
)
text = replace_once(
    text,
    "            self.panes.append(pane)\n            self.followers.append(follower)\n\n        for command_index, command in enumerate(args.commands, start=1):\n",
    '''            self.panes.append(pane)\n            self.followers.append(follower)\n            if str(path) != "-":\n                self._known_file_paths.add(Path(os.path.abspath(os.fspath(path))))\n                self.native_watch.add_file(path)\n\n        for command_index, command in enumerate(args.commands, start=1):\n''',
    "app register watched paths",
)
text = replace_once(
    text,
    "    def _stream_initial(self, index: int, pane: Pane, raw_lines: Sequence[str]) -> None:\n",
    '''    def _add_dynamic_file(self, path: Path) -> bool:\n        normalized = Path(os.path.abspath(os.fspath(path)))\n        if normalized in self._known_file_paths:\n            return False\n        highlighter = core.SyntaxHighlighter(path, self.args.syntax, self.color)\n        pane = Pane(path, highlighter, self.display_filter, self.color, self.args.idle_warn)\n        follower = FileFollower(path, self.args)\n        notice = follower.initialize_if_available()\n        if notice and notice.initial_tail is not None:\n            pane.add_initial(notice.initial_tail)\n            pane.set_snapshot(follower.previous)\n            if notice.initial_tail:\n                self._stream_initial(len(self.panes), pane, notice.initial_tail)\n        else:\n            pane.waiting = True\n            if notice and notice.kind == "error":\n                pane.add_system_line(notice.text, warning=True)\n        if highlighter.warning:\n            pane.add_system_line(highlighter.warning, warning=True)\n        self.paths.append(path)\n        self.panes.append(pane)\n        self.followers.append(follower)\n        self._known_file_paths.add(normalized)\n        self.native_watch.add_file(path)\n        self.set_message(f"glob added {pane.name}", 4.0)\n        return True\n\n    def _refresh_globs(self, now: float, events: Optional[FsEvents] = None) -> None:\n        if not self.glob_trackers:\n            return\n        event_wakeup = bool(events and (events.paths or events.directories))\n        if not event_wakeup and now < self._next_glob_scan:\n            return\n        self._next_glob_scan = now + 2.0\n        for tracker in self.glob_trackers:\n            self.native_watch.add_directory(tracker.root)\n            for path in tracker.scan():\n                self._add_dynamic_file(path)\n\n    def close_native_watch(self) -> None:\n        self.native_watch.close()\n\n    def _stream_initial(self, index: int, pane: Pane, raw_lines: Sequence[str]) -> None:\n''',
    "app dynamic glob methods",
)
text = replace_once(
    text,
    "    def _help_lines(self, width: int, height: int) -> List[str]:\n",
    '''    def _prompt_lines(self, width: int, height: int) -> List[str]:\n        mode = self.prompt_mode or "search"\n        title = "Regex search" if mode == "search" else "Regex highlight"\n        prefix = "/" if mode == "search" else "highlight: "\n        content = [\n            core.paint(prefix + self.prompt_buffer, core.BOLD_LIGHT_CYAN, self.color),\n            "",\n            "Enter apply · Esc cancel · Backspace edit",\n        ]\n        if mode == "highlight":\n            content.append("Use H from the viewer to clear the active highlight.")\n        return _panel_lines(title, content, width, height, self.color)\n\n    def _active_pane_geometry(self) -> Tuple[int, int]:\n        target = -1 if self.layout == "stream" else self.focus\n        rect = next((rect for index, rect in self.last_rects if index == target), None)\n        if rect is None:\n            width, height, _ = self.content_dimensions()\n            return max(1, width - 2), max(1, height - 2)\n        return max(1, rect.width - 2), max(1, rect.height - 2)\n\n    def _help_lines(self, width: int, height: int) -> List[str]:\n''',
    "app prompt helpers",
)
text = replace_once(
    text,
    "            \"Focused pane\",\n            \"  ↑ ↓ / PgUp PgDn    scroll\",\n",
    "            \"Focused pane\",\n            \"  /                  regex search; n / N next / previous match\",\n            \"  h                  set regex highlight; H clears it\",\n            \"  ↑ ↓ / PgUp PgDn    scroll\",\n",
    "app help search controls",
)
# Replace render() wholesale up to __enter__ to build a frame once and emit only changed rows.
start = text.index("    def render(self) -> None:\n")
end = text.index("    def __enter__(self) -> \"MultiApp\":\n", start)
new_render = '''    def _frame_rows(self) -> Tuple[int, List[str]]:\n        width, body_height, footer_height = self.content_dimensions()\n        base_body = self._pane_boxes(width, body_height)\n        if self.prompt_mode:\n            body = _overlay_modal(base_body, self._prompt_lines(width, body_height), width, body_height, self.color)\n        elif self.update_confirm_active:\n            body = _overlay_modal(base_body, self._update_lines(width, body_height), width, body_height, self.color)\n        elif self.layout_menu:\n            body = _overlay_modal(base_body, self._layout_menu_lines(width, body_height), width, body_height, self.color)\n        elif self.help_active:\n            body = _overlay_modal(base_body, self._help_lines(width, body_height), width, body_height, self.color)\n        else:\n            body = base_body\n\n        if self.prompt_mode:\n            status = ["REGEX · Enter apply · Esc cancel", "Background watching continues while this dialog is open"]\n        elif self.update_confirm_active:\n            status = ["UPDATE · y confirm · n cancel", "Background watching continues while this dialog is open"]\n        elif self.layout_menu:\n            status = ["LAYOUT · a/r/c/g/s choose · l/Esc cancel", "Background watching continues while this dialog is open"]\n        elif self.help_active:\n            status = ["HELP · ? close · q quit", "Background watching continues while this dialog is open"]\n        else:\n            status = self._status_lines(width, body_height)\n\n        frame = [_pad(body[row] if row < len(body) else "", width) for row in range(body_height)]\n        for i in range(footer_height):\n            line = status[i] if i < len(status) else ""\n            line = _pad(core.clip_ansi(line, width), width)\n            if self.color:\n                line = core.REVERSE + line + core.RESET\n            frame.append(line)\n        return width, frame\n\n    def render(self) -> None:\n        if not self.dirty:\n            return\n        width, frame = self._frame_rows()\n        geometry = (width, len(frame))\n        full = self._last_frame is None or self._last_frame_geometry != geometry\n        changed = list(range(len(frame))) if full else _changed_frame_rows(self._last_frame, frame)\n        if full:\n            sys.stdout.write(core.CLEAR_SCREEN)\n        for row in changed:\n            sys.stdout.write(f"\\033[{row + 1};1H" + core.RESET + core.CLEAR_LINE)\n            sys.stdout.write(frame[row] + core.RESET)\n        if changed:\n            sys.stdout.flush()\n        self.render_rows_written += len(changed)\n        self.render_frames += 1\n        self._last_frame = list(frame)\n        self._last_frame_geometry = geometry\n        self.dirty = False\n\n'''
text = text[:start] + new_render + text[end:]
text = replace_once(
    text,
    "        self.dirty = True\n        return self\n",
    "        self._last_frame = None\n        self._last_frame_geometry = None\n        self.dirty = True\n        return self\n",
    "app reset frame",
)
text = replace_once(
    text,
    "        for follower in self.followers:\n            close = getattr(follower, \"close\", None)\n",
    "        self.close_native_watch()\n        for follower in self.followers:\n            close = getattr(follower, \"close\", None)\n",
    "app close native watcher",
)
# Insert prompt handling before mouse/q handling.
text = replace_once(
    text,
    "    def handle_input(self, event: InputEvent) -> bool:\n        if isinstance(event, MouseEvent):\n",
    '''    def handle_input(self, event: InputEvent) -> bool:\n        if self.prompt_mode and not isinstance(event, MouseEvent):\n            key = event\n            if key == "ESC":\n                self.prompt_mode = None\n                self.prompt_buffer = ""\n                self.dirty = True\n                return False\n            if key in ("\\r", "\\n"):\n                pane = self.active_pane()\n                flags = re.IGNORECASE if self.args.ignore_case else 0\n                if self.prompt_mode == "search":\n                    error = pane.set_search(self.prompt_buffer, flags)\n                    if error is None:\n                        inner_w, body_h = self._active_pane_geometry()\n                        pane.search_next(False, inner_w, body_h)\n                else:\n                    error = pane.set_highlight(self.prompt_buffer, flags)\n                    if error is None:\n                        pane.set_message(f"highlight /{self.prompt_buffer}/" if self.prompt_buffer else "regex highlight cleared")\n                if error is not None:\n                    self.set_message(f"invalid regex: {error}", 5.0)\n                self.prompt_mode = None\n                self.prompt_buffer = ""\n                self.dirty = True\n                return False\n            if key in ("\\x7f", "\\b"):\n                self.prompt_buffer = self.prompt_buffer[:-1]\n                self.dirty = True\n                return False\n            if isinstance(key, str) and len(key) == 1 and key.isprintable():\n                self.prompt_buffer += key\n                self.dirty = True\n            return False\n\n        if isinstance(event, MouseEvent):\n''',
    "app prompt input",
)
# Insert search/highlight keys before update key.
text = replace_once(
    text,
    "        if key in (\"u\", \"U\"):\n",
    '''        if key == "/":\n            self.prompt_mode = "search"\n            self.prompt_buffer = self.active_pane().search_pattern\n            self.dirty = True\n            return False\n        if key == "h":\n            self.prompt_mode = "highlight"\n            self.prompt_buffer = self.active_pane().highlight_pattern\n            self.dirty = True\n            return False\n        if key == "H":\n            self.active_pane().clear_highlight()\n            self.dirty = True\n            return False\n        if key in ("n", "N"):\n            pane = self.active_pane()\n            inner_w, body_h = self._active_pane_geometry()\n            pane.search_next(key == "N", inner_w, body_h)\n            self.dirty = True\n            return False\n\n        if key in ("u", "U"):\n''',
    "app search keys",
)
# Auto-open the modal after a manual u-triggered refresh finds a release.
text = replace_once(
    text,
    "            if release is not None:\n                self.update_manual_check_pending = False\n                self.set_message(f\"update {release.version} available — press u\", 5.0)\n",
    '''            if release is not None:\n                if self.update_manual_check_pending:\n                    self.update_manual_check_pending = False\n                    self.update_confirm_active = True\n                    self.dirty = True\n                else:\n                    self.set_message(f"update {release.version} available — press u", 5.0)\n''',
    "app single-u update flow",
)
# Native wakeups and dynamic glob refresh before follower polls.
text = replace_once(
    text,
    "    def process_watchers(self, now: float) -> None:\n        for index, follower in enumerate(self.followers):\n",
    '''    def process_watchers(self, now: float) -> None:\n        events = self.native_watch.poll()\n        if self.native_watch.available:\n            exact_paths = {Path(os.path.abspath(os.fspath(path))) for path in events.paths}\n            dirty_dirs = {Path(os.path.abspath(os.fspath(path))) for path in events.directories}\n            for follower in self.followers:\n                if not isinstance(follower, FileFollower):\n                    continue\n                path = Path(os.path.abspath(os.fspath(follower.path)))\n                if self.native_watch.backend == "inotify":\n                    if path in exact_paths or path.parent in exact_paths:\n                        follower.notify()\n                elif path.parent in dirty_dirs:\n                    follower.notify()\n        else:\n            # Poll fallback intentionally preserves the exact v0.9 scheduling.\n            for follower in self.followers:\n                if isinstance(follower, FileFollower):\n                    follower.notify()\n\n        self._refresh_globs(now, events)\n        for index, follower in enumerate(self.followers):\n''',
    "app native watcher processing",
)
text = replace_once(
    text,
    "        controls = \"Tab pane · click focus · l layout · z max · ↑↓/Pg scroll · [/] update · f newest · p pause · u check · q quit · ? help\"\n",
    "        controls = \"/ search · n/N match · h highlight · Tab pane · l layout · ↑↓/Pg scroll · [/] update · f newest · p pause · u update · q quit · ? help\"\n",
    "app footer controls",
)
# Main/usage accept explicit globs and piped stdin rules.
text = replace_once(
    text,
    "    if not args.files and not args.commands and not sys.stdin.isatty():\n",
    "    if not args.files and not args.commands and not args.globs and not sys.stdin.isatty():\n",
    "app stdin glob condition",
)
text = replace_once(
    text,
    "    if not args.files and not args.commands:\n",
    "    if not args.files and not args.commands and not args.globs:\n",
    "app usage glob condition",
)
text = replace_once(
    text,
    "        print(\"Usage: ht FILE [FILE ...] | producer | ht | ht --exec COMMAND\")\n",
    "        print(\"Usage: ht FILE [FILE ...] | ht --glob 'logs/*.log' | producer | ht | ht --exec COMMAND\")\n",
    "app usage text",
)
text = replace_once(
    text,
    "    interactive = sys.stdout.isatty() and (sys.stdin.isatty() or has_stdin_source or bool(args.commands))\n",
    "    interactive = sys.stdout.isatty() and (sys.stdin.isatty() or has_stdin_source or bool(args.commands) or bool(args.globs))\n",
    "app interactive glob",
)
write(path, text)

print("0.10.0 integration patch applied")
