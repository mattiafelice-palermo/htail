from pathlib import Path


def replace_once(path, old, new, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path, start, end, new, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    i = text.find(start)
    j = text.find(end, i + len(start))
    if i < 0 or j < 0:
        raise RuntimeError(f"{label}: markers missing")
    p.write_text(text[:i] + new + text[j:], encoding="utf-8")


APP = "src/htail_app/app.py"

replace_once(
    APP,
    "from .extras import is_compressed_path, markdown_outline, parse_duration, syntax_path_for_source\nfrom .searching import GlobalSearchMatch, SEARCH_BOOLEAN, SEARCH_REGEX, SEARCH_SIMPLE, compile_search, preview_around_match, search_label\n",
    "from .extras import is_compressed_path, markdown_outline, parse_duration, syntax_path_for_source\nfrom .global_search import SORT_FILE, SORT_RELEVANCE, build_corpus, fuzzy_backend, render_global_search, search_corpus\nfrom .searching import GlobalSearchMatch, SEARCH_BOOLEAN, SEARCH_FUZZY, SEARCH_REGEX, SEARCH_SIMPLE, compile_search, search_label, simple_escape\n",
    "imports",
)

replace_once(
    APP,
    '    parser.add_argument("--no-native-watch", action="store_true", help="disable native filesystem notifications and use polling only")\n    return parser\n',
    '    parser.add_argument("--no-native-watch", action="store_true", help="disable native filesystem notifications and use polling only")\n    parser.add_argument("--bundle-self-test", action="store_true", help=argparse.SUPPRESS)\n    return parser\n',
    "bundle self test parser",
)

replace_once(
    APP,
    '''        self.global_search_active = False\n        self.global_search_buffer = ""\n        self.global_search_mode = SEARCH_SIMPLE\n        self.global_search_results: List[GlobalSearchMatch] = []\n        self.global_search_selected = 0\n        self.global_search_error: Optional[str] = None\n        self.global_search_truncated = False\n''',
    '''        self.global_search_active = False\n        self.global_search_buffer = ""\n        self.global_search_mode = SEARCH_SIMPLE\n        self.global_search_results: List[GlobalSearchMatch] = []\n        self.global_search_selected = 0\n        self.global_search_error: Optional[str] = None\n        self.global_search_truncated = False\n        self.global_search_ignore_case = bool(args.ignore_case)\n        self.global_search_sort = SORT_FILE\n        self.global_search_file_filter: Optional[int] = None\n        self.global_search_preview = True\n        self._global_search_corpus_signature = None\n        self._global_search_corpus = []\n        self._global_search_cache_key = None\n''',
    "global search state",
)

replace_once(
    APP,
    '''    def _local_search_flags(self) -> int:\n        return re.IGNORECASE if self.prompt_ignore_case else 0\n\n    @staticmethod\n    def _other_search_mode(mode: str) -> str:\n        return {SEARCH_SIMPLE: SEARCH_REGEX, SEARCH_REGEX: SEARCH_BOOLEAN, SEARCH_BOOLEAN: SEARCH_SIMPLE}.get(mode, SEARCH_SIMPLE)\n\n    @staticmethod\n    def _search_mode_name(mode: str) -> str:\n        return {SEARCH_SIMPLE: "Simple", SEARCH_REGEX: "Regex", SEARCH_BOOLEAN: "Boolean"}.get(mode, mode.title())\n''',
    '''    def _local_search_flags(self) -> int:\n        return re.IGNORECASE if self.prompt_ignore_case else 0\n\n    def _global_search_flags(self) -> int:\n        return re.IGNORECASE if self.global_search_ignore_case else 0\n\n    @staticmethod\n    def _other_search_mode(mode: str) -> str:\n        # Local search deliberately stays Simple / Regex / Boolean. Fuzzy is a\n        # global ranking operation rather than a per-pane regex-like matcher.\n        return {SEARCH_SIMPLE: SEARCH_REGEX, SEARCH_REGEX: SEARCH_BOOLEAN, SEARCH_BOOLEAN: SEARCH_SIMPLE}.get(mode, SEARCH_SIMPLE)\n\n    @staticmethod\n    def _other_global_search_mode(mode: str, backwards: bool = False) -> str:\n        modes = (SEARCH_SIMPLE, SEARCH_REGEX, SEARCH_BOOLEAN, SEARCH_FUZZY)\n        try:\n            index = modes.index(mode)\n        except ValueError:\n            index = 0\n        return modes[(index + (-1 if backwards else 1)) % len(modes)]\n\n    @staticmethod\n    def _search_mode_name(mode: str) -> str:\n        return {SEARCH_SIMPLE: "Simple", SEARCH_REGEX: "Regex", SEARCH_BOOLEAN: "Boolean", SEARCH_FUZZY: "Fuzzy"}.get(mode, mode.title())\n''',
    "search mode helpers",
)

new_global_methods = '''    def _global_search_signature(self):\n        return tuple(\n            (len(pane.snapshot_raw), pane.last_update_monotonic, pane.missing, pane.waiting, pane.name)\n            for pane in self.panes\n        )\n\n    def _global_search_corpus_data(self):\n        signature = self._global_search_signature()\n        if signature != self._global_search_corpus_signature:\n            self._global_search_corpus_signature = signature\n            self._global_search_corpus = build_corpus(self.panes)\n            self._global_search_cache_key = None\n        return signature, self._global_search_corpus\n\n    def _refresh_global_search_results(self) -> None:\n        signature, corpus = self._global_search_corpus_data()\n        key = (\n            self.global_search_buffer,\n            self.global_search_mode,\n            self.global_search_ignore_case,\n            self.global_search_sort,\n            self.global_search_file_filter,\n            signature,\n        )\n        if key == self._global_search_cache_key:\n            return\n        self._global_search_cache_key = key\n        page = search_corpus(\n            corpus,\n            self.global_search_buffer,\n            self.global_search_mode,\n            self._global_search_flags(),\n            file_filter=self.global_search_file_filter,\n            sort_mode=self.global_search_sort,\n            limit=GLOBAL_SEARCH_LIMIT,\n        )\n        self.global_search_results = page.results\n        self.global_search_error = page.error\n        self.global_search_truncated = page.truncated\n        if self.global_search_results:\n            self.global_search_selected = min(max(0, self.global_search_selected), len(self.global_search_results) - 1)\n        else:\n            self.global_search_selected = 0\n\n    def _cycle_global_search_file_filter(self, backwards: bool = False) -> None:\n        choices = [None] + list(range(len(self.panes)))\n        try:\n            index = choices.index(self.global_search_file_filter)\n        except ValueError:\n            index = 0\n        delta = -1 if backwards else 1\n        self.global_search_file_filter = choices[(index + delta) % len(choices)] if choices else None\n        self.global_search_selected = 0\n        self._refresh_global_search_results()\n\n    def _global_search_lines(self, width: int, height: int) -> List[str]:\n        self._refresh_global_search_results()\n        if self.global_search_file_filter is None:\n            file_label = "[All files]"\n        elif 0 <= self.global_search_file_filter < len(self.panes):\n            file_label = f"[{self.panes[self.global_search_file_filter].name}]"\n        else:\n            file_label = "[All files]"\n        return render_global_search(\n            width,\n            height,\n            query=self.global_search_buffer,\n            mode=self.global_search_mode,\n            mode_labels=(\n                (SEARCH_SIMPLE, "Simple"),\n                (SEARCH_REGEX, "Regex"),\n                (SEARCH_BOOLEAN, "Boolean"),\n                (SEARCH_FUZZY, "Fuzzy"),\n            ),\n            ignore_case=self.global_search_ignore_case,\n            sort_mode=self.global_search_sort,\n            file_filter_label=file_label,\n            results=self.global_search_results,\n            selected=self.global_search_selected,\n            truncated=self.global_search_truncated,\n            error=self.global_search_error,\n            panes=self.panes,\n            preview_enabled=self.global_search_preview,\n            color=self.color,\n        )\n\n    def _select_global_search_result(self) -> bool:\n        self._refresh_global_search_results()\n        if not self.global_search_results:\n            return False\n        result = self.global_search_results[self.global_search_selected]\n        if result.pane_index >= len(self.panes):\n            return False\n        if self.layout == "stream":\n            self.layout = "auto"\n            self.maximized = False\n        self.focus = result.pane_index\n        pane = self.panes[result.pane_index]\n        if self.global_search_mode == SEARCH_FUZZY:\n            fragment = result.text[result.match_start:result.match_end].strip()\n            if fragment:\n                pane.set_search(simple_escape(fragment), self._global_search_flags(), mode=SEARCH_SIMPLE)\n        else:\n            error = pane.set_search(\n                self.global_search_buffer,\n                self._global_search_flags(),\n                mode=self.global_search_mode,\n            )\n            if error is not None:\n                self.global_search_error = error\n                return False\n        inner_w, body_h = self._active_pane_geometry()\n        pane.jump_to_source_line(result.source_index, inner_w, body_h)\n        pane.set_message(\n            f"global match {result.source_index + 1}: {search_label(self.global_search_buffer, self.global_search_mode)}",\n            4.0,\n        )\n        self.global_search_active = False\n        self.global_search_error = None\n        self.dirty = True\n        return True\n\n'''
replace_between(
    APP,
    "    def _refresh_global_search_results(self) -> None:\n",
    "    def _active_pane_geometry(self) -> Tuple[int, int]:\n",
    new_global_methods,
    "global search methods",
)

new_global_input = '''        if self.global_search_active and not isinstance(event, MouseEvent):\n            key = event\n            if key == "ESC":\n                self.global_search_active = False\n                self.global_search_error = None\n                self.dirty = True\n                return False\n            if key in ("TAB", "SHIFT_TAB"):\n                self.global_search_mode = self._other_global_search_mode(self.global_search_mode, key == "SHIFT_TAB")\n                self.global_search_sort = SORT_RELEVANCE if self.global_search_mode == SEARCH_FUZZY else SORT_FILE\n                self.global_search_selected = 0\n                self._refresh_global_search_results()\n                self.dirty = True\n                return False\n            if key == "CTRL_T":\n                self.global_search_ignore_case = not self.global_search_ignore_case\n                self.global_search_selected = 0\n                self._refresh_global_search_results()\n                self.dirty = True\n                return False\n            if key == "CTRL_O":\n                if self.global_search_mode == SEARCH_FUZZY:\n                    self.global_search_sort = SORT_FILE if self.global_search_sort == SORT_RELEVANCE else SORT_RELEVANCE\n                    self.global_search_selected = 0\n                    self._refresh_global_search_results()\n                self.dirty = True\n                return False\n            if key == "CTRL_F":\n                self._cycle_global_search_file_filter(False)\n                self.dirty = True\n                return False\n            if key == "CTRL_P":\n                self.global_search_preview = not self.global_search_preview\n                self.dirty = True\n                return False\n            if key in ("UP", "DOWN", "PAGEUP", "PAGEDOWN"):\n                self._refresh_global_search_results()\n                if self.global_search_results:\n                    delta = {"UP": -1, "DOWN": 1, "PAGEUP": -8, "PAGEDOWN": 8}[key]\n                    self.global_search_selected = min(\n                        max(0, self.global_search_selected + delta),\n                        len(self.global_search_results) - 1,\n                    )\n                self.dirty = True\n                return False\n            if key in ("\\r", "\\n"):\n                self._select_global_search_result()\n                self.dirty = True\n                return False\n            if key in ("\\x7f", "\\b"):\n                self.global_search_buffer = self.global_search_buffer[:-1]\n                self.global_search_selected = 0\n                self._refresh_global_search_results()\n                self.dirty = True\n                return False\n            if isinstance(key, str) and len(key) == 1 and key.isprintable():\n                self.global_search_buffer += key\n                self.global_search_selected = 0\n                self._refresh_global_search_results()\n                self.dirty = True\n            return False\n\n'''
replace_between(
    APP,
    "        if self.global_search_active and not isinstance(event, MouseEvent):\n",
    "        if self.prompt_mode and not isinstance(event, MouseEvent):\n",
    new_global_input,
    "global search input",
)

replace_once(
    APP,
    '''        if key in ("g", "G"):\n            self.global_search_active = True\n            self.global_search_selected = 0\n            self._refresh_global_search_results()\n            self.dirty = True\n            return False\n''',
    '''        if key in ("g", "G"):\n            self.global_search_active = True\n            self.global_search_selected = 0\n            self.global_search_file_filter = None\n            self.global_search_sort = SORT_RELEVANCE if self.global_search_mode == SEARCH_FUZZY else SORT_FILE\n            self._refresh_global_search_results()\n            self.dirty = True\n            return False\n''',
    "open global search",
)

replace_once(
    APP,
    '            status = [f"GLOBAL SEARCH · {self._search_mode_name(self.global_search_mode)} · ↑↓ select · Enter jump · Tab mode · Esc close", "Background watching continues while this dialog is open"]\n',
    '            status = [f"GLOBAL SEARCH · {self._search_mode_name(self.global_search_mode)} · ↑↓ select · Enter jump · Tab mode · Ctrl+T case · Ctrl+O sort · Ctrl+F file · Ctrl+P preview · Esc close", "Background watching continues while this dialog is open"]\n',
    "global search footer status",
)

replace_once(
    APP,
    '            "  g                  live search across all watched files",\n',
    '            "  g                  global search: Simple / Regex / Boolean / Fuzzy",\n            "                     Ctrl+T case · Ctrl+O sort · Ctrl+F file · Ctrl+P preview",\n',
    "help global search",
)

replace_once(
    APP,
    '                f"Mode: {mode_name} · Tab toggles Simple / Regex",\n',
    '                f"Mode: {mode_name} · Tab cycles Simple / Regex / Boolean",\n',
    "local modal help text",
)

replace_once(
    APP,
    '''def main(argv: Optional[Sequence[str]] = None) -> int:\n    args = parse_args(argv)\n    core.enable_windows_ansi()\n    color = sys.stdout.isatty() and not args.no_color\n    maybe_offer_self_install(args, color)\n''',
    '''def main(argv: Optional[Sequence[str]] = None) -> int:\n    args = parse_args(argv)\n    core.enable_windows_ansi()\n    if args.bundle_self_test:\n        backend = fuzzy_backend()\n        if backend == "unavailable":\n            print("htail bundle self-test failed: RapidFuzz unavailable", file=sys.stderr)\n            return 1\n        print(f"htail bundle self-test: {backend}")\n        return 0\n    color = sys.stdout.isatty() and not args.no_color\n    maybe_offer_self_install(args, color)\n''',
    "bundle self test main",
)

# Update version now that product behavior and bundle format both change.
replace_once(
    "src/htail_app/__init__.py",
    'VERSION = "0.15.0"\n',
    'VERSION = "0.16.0"\n',
    "version bump",
)
