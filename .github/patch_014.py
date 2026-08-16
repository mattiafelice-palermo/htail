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
# input.py
# ---------------------------------------------------------------------------
path = "src/htail_app/input.py"
text = read(path)
text = replace_once(
    text,
    'InputEvent = Union[str, MouseEvent]\n_SGR_MOUSE = re.compile(r"^\\x1b\\[<(\\d+);(\\d+);(\\d+)([Mm])$")\n',
    'InputEvent = Union[str, MouseEvent]\n_SGR_MOUSE = re.compile(r"^\\x1b\\[<(\\d+);(\\d+);(\\d+)([Mm])$")\n\n\ndef normalize_plain_key(ch: str) -> str:\n    """Normalize single-byte terminal keys consistently across platforms."""\n    return {"\\t": "TAB", "\\x1b": "ESC", "\\x14": "CTRL_T"}.get(ch, ch)\n',
    "input key normalizer",
)
text = replace_once(
    text,
    '            return "TAB" if ch == "\\t" else ch\n',
    '            return normalize_plain_key(ch)\n',
    "windows plain key normalization",
)
text = replace_once(
    text,
    '            if ch != "\\x1b":\n                return "TAB" if ch == "\\t" else ch\n',
    '            if ch != "\\x1b":\n                return normalize_plain_key(ch)\n',
    "posix plain key normalization",
)
write(path, text)


# ---------------------------------------------------------------------------
# pane.py
# ---------------------------------------------------------------------------
path = "src/htail_app/pane.py"
text = read(path)
text = replace_once(
    text,
    'FOLLOW_CHANGES = "changes"\nFOLLOW_TAIL = "tail"\n',
    'FOLLOW_CHANGES = "changes"\nFOLLOW_TAIL = "tail"\nSELECTED_SEARCH_STYLE = "\\x1b[1;30;48;5;208m"\n',
    "selected search style constant",
)
text = replace_once(
    text,
    '    selected_on = "\\x1b[1;30;103m"\n',
    '    selected_on = SELECTED_SEARCH_STYLE\n',
    "selected search orange style",
)
text = replace_once(
    text,
    '        self.search_pattern = ""\n        self.search_mode = SEARCH_SIMPLE\n        self.search_regex: Optional[Pattern[str]] = None\n',
    '        self.search_pattern = ""\n        self.search_mode = SEARCH_SIMPLE\n        self.search_flags = 0\n        self.search_regex: Optional[Pattern[str]] = None\n',
    "search flags state",
)
old_set = '''    def set_search(self, expression: str, flags: int = 0, mode: str = SEARCH_REGEX) -> Optional[str]:\n        if not expression:\n            self.search_pattern = ""\n            self.search_mode = mode\n            self.search_regex = None\n            self._search_last_target = None\n            self._search_match_position = None\n            self._search_match_total = 0\n            self._mark_layout_dirty()\n            self._snapshot_layout_dirty = True\n            return None\n        compiled, error = compile_search(expression, mode, flags)\n        if error is not None:\n            return error\n        self.search_pattern = expression\n        self.search_mode = mode\n        self.search_regex = compiled\n        self._search_last_target = None\n        self._refresh_search_position()\n        self._mark_layout_dirty()\n        self._snapshot_layout_dirty = True\n        return None\n'''
new_set = '''    def set_search(self, expression: str, flags: int = 0, mode: str = SEARCH_REGEX) -> Optional[str]:\n        if not expression:\n            self.search_pattern = ""\n            self.search_mode = mode\n            self.search_flags = flags\n            self.search_regex = None\n            self._search_last_target = None\n            self._search_match_position = None\n            self._search_match_total = 0\n            self._mark_layout_dirty()\n            self._snapshot_layout_dirty = True\n            return None\n        compiled, error = compile_search(expression, mode, flags)\n        if error is not None:\n            return error\n        self.search_pattern = expression\n        self.search_mode = mode\n        self.search_flags = flags\n        self.search_regex = compiled\n        self._search_last_target = None\n        self._refresh_search_position()\n        self._mark_layout_dirty()\n        self._snapshot_layout_dirty = True\n        return None\n'''
text = replace_once(text, old_set, new_set, "set search flags")
old_state = '''    def search_state(self) -> Tuple[str, str, Optional[int]]:\n        return self.search_pattern, self.search_mode, self._search_last_target\n\n    def restore_search_state(self, state: Tuple[str, str, Optional[int]], flags: int = 0) -> None:\n        expression, mode, target = state\n        error = self.set_search(expression, flags, mode=mode)\n        if error is None and target is not None and target in self._search_candidates():\n            self._set_search_target(target)\n\n    def search_badge_text(self) -> Optional[str]:\n        if self.search_regex is None:\n            return None\n        if self._search_match_position is not None:\n            return f"MATCH {self._search_match_position}/{self._search_match_total}"\n        if self._search_match_total == 1:\n            return "1 MATCH"\n        return f"{self._search_match_total} MATCHES"\n'''
new_state = '''    def search_state(self) -> Tuple[str, str, int, Optional[int]]:\n        return self.search_pattern, self.search_mode, self.search_flags, self._search_last_target\n\n    def restore_search_state(self, state: Tuple[str, str, int, Optional[int]]) -> None:\n        expression, mode, flags, target = state\n        error = self.set_search(expression, flags, mode=mode)\n        if error is None and target is not None and target in self._search_candidates():\n            self._set_search_target(target)\n\n    def search_badge_text(self) -> Optional[str]:\n        if self.search_regex is None:\n            return None\n        if self._search_match_total <= 0:\n            return "0 MATCHES"\n        position = self._search_match_position or 0\n        return f"{position}/{self._search_match_total} MATCHES"\n'''
text = replace_once(text, old_state, new_state, "search state and badge")
marker = '''    def search_next(self, reverse: bool, width: int, body_height: int) -> bool:\n'''
insert = '''    def select_search_match(self, ordinal: int, width: int, body_height: int) -> bool:\n        """Select one current match by ordinal without emitting a transient message."""\n        candidates = self._refresh_search_position()\n        if not candidates:\n            self._search_last_target = None\n            self._search_match_position = None\n            self._mark_layout_dirty()\n            self._snapshot_layout_dirty = True\n            return False\n        target = candidates[ordinal % len(candidates)]\n        width = max(1, width)\n        body_height = max(1, body_height)\n        self._startup_follow_eof = False\n        if self.follow_mode == FOLLOW_TAIL:\n            self.tail_auto_follow = False\n        if self.snapshot_raw:\n            self.prefer_snapshot = True\n            self._snapshot_anchor_pending = False\n            self._snapshot_tail_pending = False\n            self._ensure_snapshot_layout(width)\n            self._snapshot_top = min(\n                self._snapshot_source_to_visual[target],\n                self._snapshot_max_top(body_height),\n            )\n        else:\n            self._ensure_layout(width)\n            self.top = min(self._logical_to_visual[target], self._max_top(body_height))\n        self._set_search_target(target)\n        return True\n\n    def search_next(self, reverse: bool, width: int, body_height: int) -> bool:\n'''
text = replace_once(text, marker, insert, "select search match helper")
old_top = '''        badge_text = self.search_badge_text()\n        badge_plain = f"┤ {badge_text} ├" if badge_text else ""\n        # On very narrow panes, preserve the filename/state title rather than\n        # squeezing both labels into unreadable fragments.\n        if badge_plain and width < len(badge_plain) + 12:\n            badge_plain = ""\n            badge_text = None\n        badge_visible = len(badge_plain)\n        title_room = max(1, width - 4 - badge_visible)\n        title = self.title(index, title_room, focused, body_h)\n        title = core.clip_ansi(title, title_room)\n        visible = len(core.strip_ansi(title))\n        remaining = max(0, width - 4 - visible - badge_visible)\n        if badge_plain:\n            top_plain = "╭─" + core.strip_ansi(title) + "─" * remaining + badge_plain + "─╮"\n        else:\n            top_plain = "╭─" + core.strip_ansi(title) + "─" * remaining + "─╮"\n        if self.color:\n            border_style = core.BOLD_LIGHT_CYAN if focused else core.DIM\n            top = core.paint("╭─", border_style, True) + title + core.paint("─" * remaining, border_style, True)\n            if badge_text is not None:\n                top += core.paint("┤", border_style, True)\n                badge_style = "\\x1b[1;30;106m" if focused else core.DIM\n                top += core.paint(f" {badge_text} ", badge_style, True)\n                top += core.paint("├─╮", border_style, True)\n            else:\n                top += core.paint("─╮", border_style, True)\n            side = core.paint("│", core.BOLD_LIGHT_CYAN if focused else core.DIM, True)\n        else:\n            top = top_plain\n            side = "│"\n            border_style = ""\n'''
new_top = '''        title = self.title(index, max(1, width - 4), focused, body_h)\n        title = core.clip_ansi(title, max(1, width - 4))\n        visible = len(core.strip_ansi(title))\n        remaining = max(1, width - 3 - visible)\n        top_plain = "╭─" + core.strip_ansi(title) + "─" * remaining + "╮"\n        if self.color:\n            border_style = core.BOLD_LIGHT_CYAN if focused else core.DIM\n            top = core.paint("╭─", border_style, True) + title + core.paint("─" * remaining + "╮", border_style, True)\n            side = core.paint("│", border_style, True)\n        else:\n            top = top_plain\n            side = "│"\n            border_style = ""\n'''
text = replace_once(text, old_top, new_top, "remove border match badge")
write(path, text)


# ---------------------------------------------------------------------------
# app.py
# ---------------------------------------------------------------------------
path = "src/htail_app/app.py"
text = read(path)
text = replace_once(
    text,
    '        self.prompt_error: Optional[str] = None\n        self.prompt_restore_state: Optional[Tuple[str, str, Optional[int]]] = None\n',
    '        self.prompt_error: Optional[str] = None\n        self.prompt_restore_state: Optional[Tuple[str, str, int, Optional[int]]] = None\n        self.prompt_ignore_case = bool(args.ignore_case)\n',
    "prompt case state",
)
old_boxes = '''            focused = index == self.focus if index >= 0 else True\n            box_index = index if index >= 0 else 0\n            inline_search = self.prompt_mode == "search" and focused and rect.height >= 4\n            render_height = rect.height - 1 if inline_search else rect.height\n            box = pane.render_box(rect.width, render_height, focused, box_index)\n            if inline_search:\n                box.insert(max(1, len(box) - 1), self._inline_search_row(rect.width, pane))\n'''
new_boxes = '''            focused = index == self.focus if index >= 0 else True\n            box_index = index if index >= 0 else 0\n            inline_search = self.prompt_mode == "search" and focused and rect.height >= 4\n            match_status = pane.search_regex is not None and rect.height >= (5 if inline_search else 4)\n            reserve = (1 if inline_search else 0) + (1 if match_status else 0)\n            render_height = rect.height - reserve\n            box = pane.render_box(rect.width, render_height, focused, box_index)\n            if match_status:\n                box.insert(1, self._match_status_row(rect.width, pane, focused))\n            if inline_search:\n                box.insert(max(1, len(box) - 1), self._inline_search_row(rect.width, pane))\n'''
text = replace_once(text, old_boxes, new_boxes, "pane search rows")
start = text.index('    def _search_flags(self) -> int:\n')
end = text.index('    def _prompt_lines(self, width: int, height: int) -> List[str]:\n', start)
old_helpers = text[start:end]
new_helpers = '''    def _search_flags(self) -> int:\n        return re.IGNORECASE if self.args.ignore_case else 0\n\n    def _local_search_flags(self) -> int:\n        return re.IGNORECASE if self.prompt_ignore_case else 0\n\n    @staticmethod\n    def _other_search_mode(mode: str) -> str:\n        return SEARCH_REGEX if mode == SEARCH_SIMPLE else SEARCH_SIMPLE\n\n    @staticmethod\n    def _search_mode_name(mode: str) -> str:\n        return "Simple" if mode == SEARCH_SIMPLE else "Regex"\n\n    def _preview_local_search(self) -> None:\n        if self.prompt_mode != "search":\n            return\n        pane = self.active_pane()\n        self.prompt_error = pane.set_search(\n            self.prompt_buffer,\n            self._local_search_flags(),\n            mode=self.prompt_search_mode,\n        )\n        if self.prompt_error is None and self.prompt_buffer:\n            inner_w, body_h = self._active_pane_geometry()\n            pane.select_search_match(0, inner_w, body_h)\n\n    def _cancel_local_search(self) -> None:\n        pane = self.active_pane()\n        if self.prompt_restore_state is not None:\n            pane.restore_search_state(self.prompt_restore_state)\n        self.prompt_restore_state = None\n        self.prompt_error = None\n        self.prompt_mode = None\n        self.prompt_buffer = ""\n\n    def _match_status_row(self, width: int, pane: Pane, focused: bool) -> str:\n        width = max(1, width)\n        if width < 4:\n            return " " * width\n        inner = width - 2\n        label = pane.search_badge_text() or "0 MATCHES"\n        badge_plain = f" {label} "\n        if len(badge_plain) > inner:\n            badge_plain = badge_plain[-inner:]\n        gap = max(0, inner - len(badge_plain))\n        if self.color:\n            side_style = core.BOLD_LIGHT_CYAN if focused else core.DIM\n            side = core.paint("│", side_style, True)\n            badge_style = "\\x1b[1;30;106m" if focused else core.DIM\n            content = (" " * gap) + core.paint(badge_plain, badge_style, True)\n            return _pad(side + _pad(content, inner) + side, width)\n        return _pad("│" + (" " * gap) + badge_plain + "│", width)\n\n    def _inline_search_row(self, width: int, pane: Pane) -> str:\n        width = max(1, width)\n        if width < 4:\n            return " " * width\n        inner = width - 2\n        mode_name = self._search_mode_name(self.prompt_search_mode)\n        next_mode = self._search_mode_name(self._other_search_mode(self.prompt_search_mode))\n        case_name = "NoCase" if self.prompt_ignore_case else "Case"\n        next_case = "Case" if self.prompt_ignore_case else "NoCase"\n        if self.prompt_error:\n            hints = f"INVALID REGEX · Tab→{next_mode} · Ctrl+T→{next_case} · Esc"\n        elif self.prompt_buffer:\n            hints = f"↑↓ matches · Tab→{next_mode} · Ctrl+T→{next_case} · Esc"\n        else:\n            hints = f"Tab→{next_mode} · Ctrl+T→{next_case} · Esc"\n        suffix_plain = f"  {mode_name} · {case_name} · {hints}"\n        fixed = 2 + 1 + 1 + len(suffix_plain)\n        query_room = max(0, inner - fixed)\n        query = self.prompt_buffer\n        if len(query) > query_room:\n            if query_room <= 1:\n                query = query[-query_room:] if query_room else ""\n            else:\n                query = "…" + query[-(query_room - 1):]\n        left_plain = "/ " + query + "▌"\n        gap = max(1, inner - len(left_plain) - len(suffix_plain))\n\n        if self.color:\n            side = core.paint("│", core.BOLD_LIGHT_CYAN, True)\n            prefix = core.paint("/ ", core.BOLD_LIGHT_CYAN, True)\n            cursor = core.paint("▌", core.BOLD_LIGHT_CYAN, True)\n            mode = core.paint(mode_name, "\\x1b[1;30;106m", True)\n            case = core.paint(case_name, "\\x1b[1;30;106m", True)\n            hint_style = core.BOLD_YELLOW if self.prompt_error else core.DIM\n            hint_text = core.paint(hints, hint_style, True)\n            content = prefix + query + cursor + (" " * gap) + "  " + mode + " · " + case + " · " + hint_text\n            return _pad(side + _pad(content, inner) + side, width)\n\n        content = left_plain + (" " * gap) + suffix_plain\n        return _pad("│" + _pad(content, inner) + "│", width)\n\n'''
text = text[:start] + new_helpers + text[end:]
old_geom = '''    def _active_pane_geometry(self) -> Tuple[int, int]:\n        target = -1 if self.layout == "stream" else self.focus\n        rect = next((rect for index, rect in self.last_rects if index == target), None)\n        reserve = 1 if self.prompt_mode == "search" else 0\n        if rect is None:\n            width, height, _ = self.content_dimensions()\n            return max(1, width - 2), max(1, height - 2 - reserve)\n        return max(1, rect.width - 2), max(1, rect.height - 2 - reserve)\n'''
new_geom = '''    def _active_pane_geometry(self) -> Tuple[int, int]:\n        target = -1 if self.layout == "stream" else self.focus\n        rect = next((rect for index, rect in self.last_rects if index == target), None)\n        pane = self.active_pane()\n        reserve = (1 if self.prompt_mode == "search" else 0) + (1 if pane.search_regex is not None else 0)\n        if rect is None:\n            width, height, _ = self.content_dimensions()\n            return max(1, width - 2), max(1, height - 2 - reserve)\n        return max(1, rect.width - 2), max(1, rect.height - 2 - reserve)\n'''
text = replace_once(text, old_geom, new_geom, "active pane geometry")
text = replace_once(
    text,
    '            "  /                  search focused pane; Tab toggles Simple / Regex",\n            "  n / N              next / previous local match",\n',
    '            "  /                  inline search; ↑/↓ cycle matches while typing",\n            "  Ctrl+T             toggle Case / NoCase inside local search",\n            "  n / N              next / previous committed local match",\n',
    "help search controls",
)
old_prompt_block = '''            if key in ("TAB", "SHIFT_TAB") and self.prompt_mode == "search":\n                self.prompt_search_mode = self._other_search_mode(self.prompt_search_mode)\n                self._preview_local_search()\n                self.dirty = True\n                return False\n            if key in ("\\r", "\\n"):\n                pane = self.active_pane()\n                flags = self._search_flags()\n                if self.prompt_mode == "search":\n                    error = pane.set_search(self.prompt_buffer, flags, mode=self.prompt_search_mode)\n                    self.prompt_error = error\n                    if error is not None:\n                        self.dirty = True\n                        return False\n                    inner_w, body_h = self._active_pane_geometry()\n                    pane.search_next(False, inner_w, body_h)\n                    self.prompt_restore_state = None\n                    self.prompt_error = None\n                else:\n                    error = pane.set_highlight(self.prompt_buffer, flags)\n                    if error is None:\n                        pane.set_message(f"highlight /{self.prompt_buffer}/" if self.prompt_buffer else "regex highlight cleared")\n                    if error is not None:\n                        self.set_message(f"invalid search: {error}", 5.0)\n                self.prompt_mode = None\n                self.prompt_buffer = ""\n                self.dirty = True\n                return False\n'''
new_prompt_block = '''            if key in ("TAB", "SHIFT_TAB") and self.prompt_mode == "search":\n                self.prompt_search_mode = self._other_search_mode(self.prompt_search_mode)\n                self._preview_local_search()\n                self.dirty = True\n                return False\n            if key == "CTRL_T" and self.prompt_mode == "search":\n                self.prompt_ignore_case = not self.prompt_ignore_case\n                self._preview_local_search()\n                self.dirty = True\n                return False\n            if key in ("UP", "DOWN") and self.prompt_mode == "search":\n                if self.prompt_error is None and self.prompt_buffer:\n                    pane = self.active_pane()\n                    inner_w, body_h = self._active_pane_geometry()\n                    pane.search_next(key == "UP", inner_w, body_h)\n                self.dirty = True\n                return False\n            if key in ("\\r", "\\n"):\n                pane = self.active_pane()\n                if self.prompt_mode == "search":\n                    if self.prompt_error is not None:\n                        self.dirty = True\n                        return False\n                    self.prompt_restore_state = None\n                    self.prompt_error = None\n                else:\n                    error = pane.set_highlight(self.prompt_buffer, self._search_flags())\n                    if error is None:\n                        pane.set_message(f"highlight /{self.prompt_buffer}/" if self.prompt_buffer else "regex highlight cleared")\n                    if error is not None:\n                        self.set_message(f"invalid search: {error}", 5.0)\n                self.prompt_mode = None\n                self.prompt_buffer = ""\n                self.dirty = True\n                return False\n'''
text = replace_once(text, old_prompt_block, new_prompt_block, "prompt search interaction")
old_open = '''        if key == "/":\n            pane = self.active_pane()\n            self.prompt_restore_state = pane.search_state()\n            self.prompt_mode = "search"\n            self.prompt_buffer = pane.search_pattern\n            self.prompt_search_mode = pane.search_mode if pane.search_pattern else SEARCH_SIMPLE\n            self.prompt_error = None\n            self.dirty = True\n            return False\n'''
new_open = '''        if key == "/":\n            pane = self.active_pane()\n            self.prompt_restore_state = pane.search_state()\n            self.prompt_mode = "search"\n            self.prompt_buffer = pane.search_pattern\n            self.prompt_search_mode = pane.search_mode if pane.search_pattern else SEARCH_SIMPLE\n            self.prompt_ignore_case = bool(pane.search_flags & re.IGNORECASE) if pane.search_pattern else bool(self.args.ignore_case)\n            self.prompt_error = None\n            self.dirty = True\n            return False\n'''
text = replace_once(text, old_open, new_open, "open search case state")
text = replace_once(
    text,
    '                status = [f"SEARCH · {self._search_mode_name(self.prompt_search_mode)} · live highlight · Tab mode · Enter apply · Esc cancel", "Search field is attached to the focused pane; background watching continues"]\n',
    '                case = "NoCase" if self.prompt_ignore_case else "Case"\n                status = [f"SEARCH · {self._search_mode_name(self.prompt_search_mode)} · {case} · ↑↓ match · Tab mode · Ctrl+T case · Enter apply · Esc close", "Search field is attached to the focused pane; background watching continues"]\n',
    "search footer hints",
)
write(path, text)


# ---------------------------------------------------------------------------
# version / release notes / README
# ---------------------------------------------------------------------------
path = "src/htail_app/__init__.py"
text = read(path)
text = replace_once(text, 'VERSION = "0.13.0"\n', 'VERSION = "0.14.0"\n', "version")
write(path, text)

write("RELEASE_NOTES.md", '''# htail 0.14.0\n\n## New features\n\n- Local search now selects the first live match immediately while typing, with `↑` / `↓` cycling matches without leaving the inline editor.\n- Match progress is shown as a prominent status badge inside the pane (`1/4 MATCHES`) instead of being embedded in the top border.\n- `Ctrl+T` toggles Case / NoCase search interactively; the search row always shows the current state and the available shortcut.\n\n## Bug fixes\n\n- Fixed `Esc` handling on native Windows so the inline search reliably closes there as it already did on POSIX terminals.\n- Selected matches now use black-on-orange high-contrast styling rather than relying on syntax foreground colours.\n- Search viewport geometry now accounts for both the internal match-status row and the inline editor row.\n''')

path = "README.md"
text = read(path)
text = replace_once(
    text,
    '| `/` | Open the focused pane\'s inline live search field |\n| `n` / `N` | Jump to next / previous search match, wrapping at the ends |\n',
    '| `/` | Open the focused pane\'s inline live search field |\n| `↑` / `↓` while searching | Previous / next live match |\n| `Ctrl+T` while searching | Toggle Case / NoCase matching |\n| `n` / `N` | Jump to next / previous committed search match, wrapping at the ends |\n',
    "README controls",
)
text = replace_once(
    text,
    'Simple search is the default; `Tab` switches the inline field to explicit regex mode. `-I` / `--ignore-case` applies to both. Matches highlight live while you type. Non-selected matches use reverse video, while the currently selected `n` / `N` match uses guaranteed black-on-bright-yellow contrast. Match progress appears as a high-contrast badge on the pane\'s top-right border. Persistent regex highlights use underline so existing syntax colors remain visible.\n',
    'Simple search is the default; `Tab` switches the inline field to explicit regex mode. `-I` / `--ignore-case` sets the initial case behavior, and `Ctrl+T` toggles Case / NoCase interactively. Matches highlight live while you type: the first match is selected immediately, `↑` / `↓` cycle through results without closing the editor, and the selected match uses high-contrast black-on-orange. Match progress appears as a prominent `x/y MATCHES` badge inside the pane. Persistent regex highlights use underline so existing syntax colors remain visible.\n',
    "README search summary",
)
text = replace_once(
    text,
    'Press `/` for search inside the focused pane. A compact search field attaches to the bottom of that pane instead of opening a modal, so matching text remains visible and updates live while you type. Search opens in **Simple** mode: ordinary text is literal, `*` means any text and `?` means one character. Press `Tab` in the field to switch to explicit Python-regex mode, `Enter` to commit, or `Esc` to restore the previous search. After applying a search, `n` / `N` move between matches.\n',
    'Press `/` for search inside the focused pane. A compact search field attaches to the bottom of that pane instead of opening a modal, so matching text remains visible and updates live while you type. Search opens in **Simple** mode: ordinary text is literal, `*` means any text and `?` means one character. The first match is selected immediately; use `↑` / `↓` to cycle results while still editing. Press `Tab` to switch to explicit Python-regex mode, `Ctrl+T` to toggle Case / NoCase, `Enter` to commit, or `Esc` to restore the previous search and close the editor. After applying a search, `n` / `N` move between matches.\n',
    "README search details",
)
write(path, text)


# ---------------------------------------------------------------------------
# update legacy tests and add 0.14 regressions
# ---------------------------------------------------------------------------
path = "tests/test_follow_search_012.py"
text = read(path)
text = text.replace('self.assertIn("MATCH 1/3", core.strip_ansi(pane.render_box(100, 6, True, 0)[0]))', 'self.assertEqual(pane.search_badge_text(), "1/3 MATCHES")')
text = text.replace('self.assertIn("MATCH 2/3", core.strip_ansi(pane.render_box(100, 6, True, 0)[0]))', 'self.assertEqual(pane.search_badge_text(), "2/3 MATCHES")')
text = text.replace('"\\x1b[1;30;103m" in row', '"\\x1b[1;30;48;5;208m" in row')
write(path, text)

path = "tests/test_search_013.py"
text = read(path)
text = replace_once(text, '    def test_selected_match_is_guaranteed_black_on_bright_yellow(self):\n', '    def test_selected_match_is_guaranteed_black_on_orange(self):\n', "0.13 contrast test name")
text = text.replace('self.assertIn("\\x1b[1;30;103m", selected)', 'self.assertIn("\\x1b[1;30;48;5;208m", selected)')
old_badge_test = '''    def test_match_progress_is_a_top_right_border_badge(self):\n        pane = self.make_pane()\n        top = core.strip_ansi(pane.render_box(80, 7, True, 0)[0])\n        self.assertIn("┤ MATCH 1/3 ├", top)\n        self.assertGreater(top.index("MATCH 1/3"), 50)\n        self.assertNotIn("MATCH", core.strip_ansi(pane.title(0, 80, True, 5)))\n'''
new_badge_test = '''    def test_match_progress_is_no_longer_embedded_in_top_border(self):\n        pane = self.make_pane()\n        top = core.strip_ansi(pane.render_box(80, 7, True, 0)[0])\n        self.assertNotIn("MATCH", top)\n        self.assertEqual(pane.search_badge_text(), "1/3 MATCHES")\n'''
text = replace_once(text, old_badge_test, new_badge_test, "0.13 border test migration")
write(path, text)

path = "tests/test_multifile.py"
text = read(path)
text = replace_once(
    text,
    'from htail_app.input import MouseEvent, parse_escape_sequence\n',
    'from htail_app.input import MouseEvent, normalize_plain_key, parse_escape_sequence\n',
    "input test import",
)
text = replace_once(
    text,
    '    def test_shift_tab(self):\n        self.assertEqual(parse_escape_sequence("\\x1b[Z"), "SHIFT_TAB")\n',
    '    def test_shift_tab(self):\n        self.assertEqual(parse_escape_sequence("\\x1b[Z"), "SHIFT_TAB")\n\n    def test_plain_escape_and_ctrl_t_are_normalized(self):\n        self.assertEqual(normalize_plain_key("\\x1b"), "ESC")\n        self.assertEqual(normalize_plain_key("\\x14"), "CTRL_T")\n',
    "input normalization tests",
)
write(path, text)

write("tests/test_search_014.py", '''from __future__ import annotations\n\nfrom pathlib import Path\nimport tempfile\nimport unittest\n\nfrom htail_app import app, core\nfrom htail_app.app import MultiApp\nfrom htail_app.input import normalize_plain_key\n\n\nclass Search014Tests(unittest.TestCase):\n    def make_app(self, root: Path, *, color=False, text="alpha foo\\nbeta\\ngamma foo\\ndelta\\n"):\n        source = root / "source.txt"\n        source.write_text(text, encoding="utf-8")\n        args = app.parse_args([str(source), "--no-native-watch"] + (["--no-color"] if not color else []))\n        return MultiApp(args, color, core.DisplayFilter(), core.UpdateService(""))\n\n    def type_query(self, application: MultiApp, query: str) -> None:\n        application.handle_input("/")\n        for ch in query:\n            application.handle_input(ch)\n\n    def test_escape_key_normalization_closes_inline_search(self):\n        with tempfile.TemporaryDirectory() as td:\n            application = self.make_app(Path(td))\n            try:\n                application.handle_input("/")\n                application.handle_input("f")\n                application.handle_input(normalize_plain_key("\\x1b"))\n                self.assertIsNone(application.prompt_mode)\n                self.assertEqual(application.active_pane().search_pattern, "")\n            finally:\n                application.close_native_watch()\n\n    def test_live_typing_selects_first_match_immediately(self):\n        with tempfile.TemporaryDirectory() as td:\n            application = self.make_app(Path(td), color=True)\n            try:\n                self.type_query(application, "foo")\n                pane = application.active_pane()\n                self.assertEqual((pane._search_match_position, pane._search_match_total), (1, 2))\n                application._frame_rows()\n                selected = pane._snapshot_visual_lines[pane._snapshot_source_to_visual[pane._search_last_target]]\n                self.assertIn("\\x1b[1;30;48;5;208m", selected)\n            finally:\n                application.close_native_watch()\n\n    def test_up_down_cycle_matches_while_editor_stays_open(self):\n        with tempfile.TemporaryDirectory() as td:\n            application = self.make_app(Path(td))\n            try:\n                self.type_query(application, "foo")\n                pane = application.active_pane()\n                application.handle_input("DOWN")\n                self.assertEqual(pane._search_match_position, 2)\n                self.assertEqual(application.prompt_mode, "search")\n                application.handle_input("UP")\n                self.assertEqual(pane._search_match_position, 1)\n                self.assertEqual(application.prompt_mode, "search")\n            finally:\n                application.close_native_watch()\n\n    def test_match_badge_is_inside_panel_and_search_row_is_discoverable(self):\n        with tempfile.TemporaryDirectory() as td:\n            application = self.make_app(Path(td))\n            try:\n                self.type_query(application, "foo")\n                width, height, _ = application.content_dimensions()\n                rows = application._pane_boxes(width, height)\n                rect = application.last_rects[0][1]\n                local = [core.strip_ansi(row[rect.x:rect.x + rect.width]) for row in rows[rect.y:rect.y + rect.height]]\n                self.assertNotIn("MATCH", local[0])\n                self.assertTrue(any("1/2 MATCHES" in row for row in local[1:-1]))\n                self.assertTrue(any("↑↓ matches" in row and "Ctrl+T" in row for row in local))\n            finally:\n                application.close_native_watch()\n\n    def test_ctrl_t_toggles_case_and_live_results(self):\n        with tempfile.TemporaryDirectory() as td:\n            application = self.make_app(Path(td), text="Foo\\nfoo\\nFOO\\n")\n            try:\n                self.type_query(application, "foo")\n                pane = application.active_pane()\n                self.assertEqual(pane._search_match_total, 1)\n                self.assertFalse(application.prompt_ignore_case)\n                application.handle_input("CTRL_T")\n                self.assertTrue(application.prompt_ignore_case)\n                self.assertEqual(pane._search_match_total, 3)\n                self.assertEqual(pane._search_match_position, 1)\n                application.handle_input("CTRL_T")\n                self.assertFalse(application.prompt_ignore_case)\n                self.assertEqual(pane._search_match_total, 1)\n            finally:\n                application.close_native_watch()\n\n    def test_committed_case_mode_is_restored_on_reopen_and_escape(self):\n        with tempfile.TemporaryDirectory() as td:\n            application = self.make_app(Path(td), text="Foo\\nfoo\\n")\n            try:\n                self.type_query(application, "foo")\n                application.handle_input("CTRL_T")\n                application.handle_input("\\r")\n                pane = application.active_pane()\n                self.assertTrue(bool(pane.search_flags))\n                application.handle_input("/")\n                self.assertTrue(application.prompt_ignore_case)\n                application.handle_input("CTRL_T")\n                application.handle_input("ESC")\n                self.assertTrue(bool(pane.search_flags))\n                self.assertIsNone(application.prompt_mode)\n            finally:\n                application.close_native_watch()\n\n\nif __name__ == "__main__":\n    unittest.main()\n''')

print("htail 0.14.0 patch applied")
