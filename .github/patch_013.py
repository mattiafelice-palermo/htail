from pathlib import Path
import re


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
# pane.py
# ---------------------------------------------------------------------------
pane_path = "src/htail_app/pane.py"
pane = read(pane_path)

helper_marker = '''    return text\n\n\nclass Pane:\n'''
helper_insert = '''    return text\n\n\ndef _active_sgr_prefix(text: str, end: int) -> str:\n    \"\"\"Replay the visible SGR state active immediately before ``end``.\"\"\"\n    active: List[str] = []\n    for match in core.ANSI_RE.finditer(text[:end]):\n        seq = match.group(0)\n        if not seq.endswith("m"):\n            continue\n        if seq in ("\\x1b[0m", "\\x1b[m"):\n            active = []\n        else:\n            active.append(seq)\n    return "".join(active)\n\n\ndef _inject_selected_regex_style(text: str, pattern: Optional[Pattern[str]]) -> str:\n    \"\"\"Render selected search spans as guaranteed black-on-bright-yellow.\n\n    Syntax-highlighting SGR inside a match can otherwise turn the foreground\n    white again, which produced low-contrast white/yellow combinations. Strip\n    styling only inside the selected span, then restore the surrounding row's\n    SGR state after it.\n    \"\"\"\n    if pattern is None:\n        return text\n    plain = core.strip_ansi(text)\n    spans = [(m.start(), m.end()) for m in pattern.finditer(plain) if m.end() > m.start()]\n    if not spans:\n        return text\n\n    boundaries: List[int] = [0] * (len(plain) + 1)\n    raw = visible = 0\n    while raw < len(text) and visible < len(plain):\n        match = core.ANSI_RE.match(text, raw)\n        if match:\n            raw = match.end()\n            continue\n        boundaries[visible] = raw\n        visible += 1\n        raw += 1\n    boundaries[visible] = raw\n\n    selected_on = "\\x1b[1;30;103m"\n    for start, end in reversed(spans):\n        raw_start = boundaries[start]\n        raw_end = boundaries[end]\n        restore = _active_sgr_prefix(text, raw_start)\n        selected_plain = core.strip_ansi(text[raw_start:raw_end])\n        text = (\n            text[:raw_start]\n            + selected_on\n            + selected_plain\n            + core.RESET\n            + restore\n            + text[raw_end:]\n        )\n    return text\n\n\nclass Pane:\n'''
pane = replace_once(pane, helper_marker, helper_insert, "selected style helper")

old_apply = '''    def _apply_regex_marks(self, row: str, search_index: Optional[int] = None) -> str:\n        if not self.color:\n            return row\n        # Underline is the persistent user highlight. Non-selected search\n        # matches keep reverse video; the currently selected n/N match gets a\n        # bright-yellow background so it is immediately distinguishable while\n        # preserving the row's existing foreground/syntax colour.\n        row = _inject_regex_style(row, self.highlight_regex, "\\x1b[4m", "\\x1b[24m")\n        if self.search_regex is not None:\n            if search_index is not None and search_index == self._search_last_target:\n                row = _inject_regex_style(row, self.search_regex, "\\x1b[103m", "\\x1b[49m")\n            else:\n                row = _inject_regex_style(row, self.search_regex, "\\x1b[7m", "\\x1b[27m")\n        return row\n'''
new_apply = '''    def _apply_regex_marks(self, row: str, search_index: Optional[int] = None) -> str:\n        if not self.color:\n            return row\n        row = _inject_regex_style(row, self.highlight_regex, "\\x1b[4m", "\\x1b[24m")\n        if self.search_regex is not None:\n            if search_index is not None and search_index == self._search_last_target:\n                row = _inject_selected_regex_style(row, self.search_regex)\n            else:\n                row = _inject_regex_style(row, self.search_regex, "\\x1b[7m", "\\x1b[27m")\n        return row\n'''
pane = replace_once(pane, old_apply, new_apply, "selected search style")

state_marker = '''    def _search_display(self) -> str:\n        return search_label(self.search_pattern, self.search_mode)\n\n'''
state_insert = '''    def _search_display(self) -> str:\n        return search_label(self.search_pattern, self.search_mode)\n\n    def search_state(self) -> Tuple[str, str, Optional[int]]:\n        return self.search_pattern, self.search_mode, self._search_last_target\n\n    def restore_search_state(self, state: Tuple[str, str, Optional[int]], flags: int = 0) -> None:\n        expression, mode, target = state\n        error = self.set_search(expression, flags, mode=mode)\n        if error is None and target is not None and target in self._search_candidates():\n            self._set_search_target(target)\n\n    def search_badge_text(self) -> Optional[str]:\n        if self.search_regex is None:\n            return None\n        if self._search_match_position is not None:\n            return f"MATCH {self._search_match_position}/{self._search_match_total}"\n        if self._search_match_total == 1:\n            return "1 MATCH"\n        return f"{self._search_match_total} MATCHES"\n\n'''
pane = replace_once(pane, state_marker, state_insert, "search state helpers")

old_title_search = '''        parts = [f"{index + 1}:{self.name}", state, self.follow_mode.upper()]\n        if self.search_regex is not None:\n            position = self._search_match_position or 0\n            parts.append(f"MATCH {position}/{self._search_match_total}")\n        current = self.current_update_number()\n'''
new_title_search = '''        parts = [f"{index + 1}:{self.name}", state, self.follow_mode.upper()]\n        current = self.current_update_number()\n'''
pane = replace_once(pane, old_title_search, new_title_search, "remove crowded match title")

old_top = '''        title = self.title(index, max(1, width - 4), focused, body_h)\n        title_plain = core.strip_ansi(title)\n        title = core.clip_ansi(title, max(1, width - 4))\n        visible = len(core.strip_ansi(title))\n        # Corners + the leading separator consume three cells. The title\n        # is already clipped to width-4, guaranteeing at least one trailing\n        # dash while keeping the top border exactly `width` cells wide.\n        remaining = max(1, width - 3 - visible)\n        top_plain = "╭─" + core.strip_ansi(title) + "─" * remaining + "╮"\n        if self.color:\n            border_style = core.BOLD_LIGHT_CYAN if focused else core.DIM\n            top = core.paint("╭─", border_style, True) + title + core.paint("─" * remaining + "╮", border_style, True)\n            side = core.paint("│", core.BOLD_LIGHT_CYAN if focused else core.DIM, True)\n            border_style = core.BOLD_LIGHT_CYAN if focused else core.DIM\n        else:\n            top = top_plain\n            side = "│"\n            border_style = ""\n'''
new_top = '''        badge_text = self.search_badge_text()\n        badge_plain = f"┤ {badge_text} ├" if badge_text else ""\n        # On very narrow panes, preserve the filename/state title rather than\n        # squeezing both labels into unreadable fragments.\n        if badge_plain and width < len(badge_plain) + 12:\n            badge_plain = ""\n            badge_text = None\n        badge_visible = len(badge_plain)\n        title_room = max(1, width - 4 - badge_visible)\n        title = self.title(index, title_room, focused, body_h)\n        title = core.clip_ansi(title, title_room)\n        visible = len(core.strip_ansi(title))\n        remaining = max(0, width - 4 - visible - badge_visible)\n        if badge_plain:\n            top_plain = "╭─" + core.strip_ansi(title) + "─" * remaining + badge_plain + "─╮"\n        else:\n            top_plain = "╭─" + core.strip_ansi(title) + "─" * remaining + "─╮"\n        if self.color:\n            border_style = core.BOLD_LIGHT_CYAN if focused else core.DIM\n            top = core.paint("╭─", border_style, True) + title + core.paint("─" * remaining, border_style, True)\n            if badge_text is not None:\n                top += core.paint("┤", border_style, True)\n                badge_style = "\\x1b[1;30;106m" if focused else core.DIM\n                top += core.paint(f" {badge_text} ", badge_style, True)\n                top += core.paint("├─╮", border_style, True)\n            else:\n                top += core.paint("─╮", border_style, True)\n            side = core.paint("│", core.BOLD_LIGHT_CYAN if focused else core.DIM, True)\n        else:\n            top = top_plain\n            side = "│"\n            border_style = ""\n'''
pane = replace_once(pane, old_top, new_top, "top-right match badge")
write(pane_path, pane)


# ---------------------------------------------------------------------------
# app.py
# ---------------------------------------------------------------------------
app_path = "src/htail_app/app.py"
app = read(app_path)

old_prompt_state = '''        self.prompt_mode: Optional[str] = None\n        self.prompt_buffer = ""\n        self.prompt_search_mode = SEARCH_SIMPLE\n        self.global_search_active = False\n'''
new_prompt_state = '''        self.prompt_mode: Optional[str] = None\n        self.prompt_buffer = ""\n        self.prompt_search_mode = SEARCH_SIMPLE\n        self.prompt_error: Optional[str] = None\n        self.prompt_restore_state: Optional[Tuple[str, str, Optional[int]]] = None\n        self.global_search_active = False\n'''
app = replace_once(app, old_prompt_state, new_prompt_state, "prompt state")

search_mode_marker = '''    @staticmethod\n    def _search_mode_name(mode: str) -> str:\n        return "Simple" if mode == SEARCH_SIMPLE else "Regex"\n\n'''
search_mode_insert = '''    @staticmethod\n    def _search_mode_name(mode: str) -> str:\n        return "Simple" if mode == SEARCH_SIMPLE else "Regex"\n\n    def _preview_local_search(self) -> None:\n        if self.prompt_mode != "search":\n            return\n        self.prompt_error = self.active_pane().set_search(\n            self.prompt_buffer,\n            self._search_flags(),\n            mode=self.prompt_search_mode,\n        )\n\n    def _cancel_local_search(self) -> None:\n        pane = self.active_pane()\n        if self.prompt_restore_state is not None:\n            pane.restore_search_state(self.prompt_restore_state, self._search_flags())\n        self.prompt_restore_state = None\n        self.prompt_error = None\n        self.prompt_mode = None\n        self.prompt_buffer = ""\n\n    def _inline_search_row(self, width: int, pane: Pane) -> str:\n        width = max(1, width)\n        if width < 4:\n            return " " * width\n        inner = width - 2\n        mode_name = self._search_mode_name(self.prompt_search_mode)\n        if self.prompt_error:\n            status_text = "INVALID REGEX"\n        elif not self.prompt_buffer:\n            status_text = "type to search"\n        else:\n            count = pane._search_match_total if pane.search_regex is not None else 0\n            status_text = f"{count} match{'es' if count != 1 else ''}"\n        suffix_plain = f"  {mode_name} · {status_text}"\n        fixed = 2 + 1 + 1 + len(suffix_plain)\n        query_room = max(0, inner - fixed)\n        query = self.prompt_buffer\n        if len(query) > query_room:\n            if query_room <= 1:\n                query = query[-query_room:] if query_room else ""\n            else:\n                query = "…" + query[-(query_room - 1):]\n        left_plain = "/ " + query + "▌"\n        gap = max(1, inner - len(left_plain) - len(suffix_plain))\n\n        if self.color:\n            side = core.paint("│", core.BOLD_LIGHT_CYAN, True)\n            prefix = core.paint("/ ", core.BOLD_LIGHT_CYAN, True)\n            cursor = core.paint("▌", core.BOLD_LIGHT_CYAN, True)\n            mode = core.paint(mode_name, "\\x1b[1;30;106m", True)\n            status_style = core.BOLD_YELLOW if self.prompt_error else core.DIM\n            status = core.paint(status_text, status_style, True)\n            content = prefix + query + cursor + (" " * gap) + "  " + mode + " · " + status\n            return _pad(side + _pad(content, inner) + side, width)\n\n        content = left_plain + (" " * gap) + suffix_plain\n        return _pad("│" + _pad(content, inner) + "│", width)\n\n'''
app = replace_once(app, search_mode_marker, search_mode_insert, "inline search helpers")

old_render_box = '''            focused = index == self.focus if index >= 0 else True\n            box_index = index if index >= 0 else 0\n            box = pane.render_box(rect.width, rect.height, focused, box_index)\n            for local_y, row in enumerate(box):\n'''
new_render_box = '''            focused = index == self.focus if index >= 0 else True\n            box_index = index if index >= 0 else 0\n            inline_search = self.prompt_mode == "search" and focused and rect.height >= 4\n            render_height = rect.height - 1 if inline_search else rect.height\n            box = pane.render_box(rect.width, render_height, focused, box_index)\n            if inline_search:\n                box.insert(max(1, len(box) - 1), self._inline_search_row(rect.width, pane))\n            for local_y, row in enumerate(box):\n'''
app = replace_once(app, old_render_box, new_render_box, "inline search pane geometry")

old_geom = '''        if rect is None:\n            width, height, _ = self.content_dimensions()\n            return max(1, width - 2), max(1, height - 2)\n        return max(1, rect.width - 2), max(1, rect.height - 2)\n'''
new_geom = '''        reserve = 1 if self.prompt_mode == "search" else 0\n        if rect is None:\n            width, height, _ = self.content_dimensions()\n            return max(1, width - 2), max(1, height - 2 - reserve)\n        return max(1, rect.width - 2), max(1, rect.height - 2 - reserve)\n'''
app = replace_once(app, old_geom, new_geom, "active pane search geometry")

old_frame_modal = '''        if self.global_search_active:\n            body = _overlay_modal(base_body, self._global_search_lines(width, body_height), width, body_height, self.color)\n        elif self.prompt_mode:\n            body = _overlay_modal(base_body, self._prompt_lines(width, body_height), width, body_height, self.color)\n        elif self.update_confirm_active:\n'''
new_frame_modal = '''        if self.global_search_active:\n            body = _overlay_modal(base_body, self._global_search_lines(width, body_height), width, body_height, self.color)\n        elif self.prompt_mode and self.prompt_mode != "search":\n            body = _overlay_modal(base_body, self._prompt_lines(width, body_height), width, body_height, self.color)\n        elif self.update_confirm_active:\n'''
app = replace_once(app, old_frame_modal, new_frame_modal, "local search non-modal")

old_status = '''        elif self.prompt_mode:\n            if self.prompt_mode == "search":\n                status = [f"SEARCH · {self._search_mode_name(self.prompt_search_mode)} · Tab mode · Enter apply · Esc cancel", "Background watching continues while this dialog is open"]\n            else:\n                status = ["REGEX HIGHLIGHT · Enter apply · Esc cancel", "Background watching continues while this dialog is open"]\n'''
new_status = '''        elif self.prompt_mode:\n            if self.prompt_mode == "search":\n                status = [f"SEARCH · {self._search_mode_name(self.prompt_search_mode)} · live highlight · Tab mode · Enter apply · Esc cancel", "Search field is attached to the focused pane; background watching continues"]\n            else:\n                status = ["REGEX HIGHLIGHT · Enter apply · Esc cancel", "Background watching continues while this dialog is open"]\n'''
app = replace_once(app, old_status, new_status, "search status text")

old_prompt_handler = '''        if self.prompt_mode and not isinstance(event, MouseEvent):\n            key = event\n            if key == "ESC":\n                self.prompt_mode = None\n                self.prompt_buffer = ""\n                self.dirty = True\n                return False\n            if key in ("TAB", "SHIFT_TAB") and self.prompt_mode == "search":\n                self.prompt_search_mode = self._other_search_mode(self.prompt_search_mode)\n                self.dirty = True\n                return False\n            if key in ("\\r", "\\n"):\n                pane = self.active_pane()\n                flags = self._search_flags()\n                if self.prompt_mode == "search":\n                    error = pane.set_search(self.prompt_buffer, flags, mode=self.prompt_search_mode)\n                    if error is None:\n                        inner_w, body_h = self._active_pane_geometry()\n                        pane.search_next(False, inner_w, body_h)\n                else:\n                    error = pane.set_highlight(self.prompt_buffer, flags)\n                    if error is None:\n                        pane.set_message(f"highlight /{self.prompt_buffer}/" if self.prompt_buffer else "regex highlight cleared")\n                if error is not None:\n                    self.set_message(f"invalid search: {error}", 5.0)\n                self.prompt_mode = None\n                self.prompt_buffer = ""\n                self.dirty = True\n                return False\n            if key in ("\\x7f", "\\b"):\n                self.prompt_buffer = self.prompt_buffer[:-1]\n                self.dirty = True\n                return False\n            if isinstance(key, str) and len(key) == 1 and key.isprintable():\n                self.prompt_buffer += key\n                self.dirty = True\n            return False\n'''
new_prompt_handler = '''        if self.prompt_mode and not isinstance(event, MouseEvent):\n            key = event\n            if key == "ESC":\n                if self.prompt_mode == "search":\n                    self._cancel_local_search()\n                else:\n                    self.prompt_mode = None\n                    self.prompt_buffer = ""\n                self.dirty = True\n                return False\n            if key in ("TAB", "SHIFT_TAB") and self.prompt_mode == "search":\n                self.prompt_search_mode = self._other_search_mode(self.prompt_search_mode)\n                self._preview_local_search()\n                self.dirty = True\n                return False\n            if key in ("\\r", "\\n"):\n                pane = self.active_pane()\n                flags = self._search_flags()\n                if self.prompt_mode == "search":\n                    error = pane.set_search(self.prompt_buffer, flags, mode=self.prompt_search_mode)\n                    self.prompt_error = error\n                    if error is not None:\n                        self.dirty = True\n                        return False\n                    inner_w, body_h = self._active_pane_geometry()\n                    pane.search_next(False, inner_w, body_h)\n                    self.prompt_restore_state = None\n                    self.prompt_error = None\n                else:\n                    error = pane.set_highlight(self.prompt_buffer, flags)\n                    if error is None:\n                        pane.set_message(f"highlight /{self.prompt_buffer}/" if self.prompt_buffer else "regex highlight cleared")\n                    if error is not None:\n                        self.set_message(f"invalid search: {error}", 5.0)\n                self.prompt_mode = None\n                self.prompt_buffer = ""\n                self.dirty = True\n                return False\n            if key in ("\\x7f", "\\b"):\n                self.prompt_buffer = self.prompt_buffer[:-1]\n                if self.prompt_mode == "search":\n                    self._preview_local_search()\n                self.dirty = True\n                return False\n            if isinstance(key, str) and len(key) == 1 and key.isprintable():\n                self.prompt_buffer += key\n                if self.prompt_mode == "search":\n                    self._preview_local_search()\n                self.dirty = True\n            return False\n'''
app = replace_once(app, old_prompt_handler, new_prompt_handler, "live local search input")

old_open_search = '''        if key == "/":\n            pane = self.active_pane()\n            self.prompt_mode = "search"\n            self.prompt_buffer = pane.search_pattern\n            self.prompt_search_mode = pane.search_mode if pane.search_pattern else SEARCH_SIMPLE\n            self.dirty = True\n            return False\n'''
new_open_search = '''        if key == "/":\n            pane = self.active_pane()\n            self.prompt_restore_state = pane.search_state()\n            self.prompt_mode = "search"\n            self.prompt_buffer = pane.search_pattern\n            self.prompt_search_mode = pane.search_mode if pane.search_pattern else SEARCH_SIMPLE\n            self.prompt_error = None\n            self.dirty = True\n            return False\n'''
app = replace_once(app, old_open_search, new_open_search, "open inline search")

old_highlight_open = '''        if key == "h":\n            self.prompt_mode = "highlight"\n            self.prompt_buffer = self.active_pane().highlight_pattern\n            self.dirty = True\n            return False\n'''
new_highlight_open = '''        if key == "h":\n            self.prompt_restore_state = None\n            self.prompt_error = None\n            self.prompt_mode = "highlight"\n            self.prompt_buffer = self.active_pane().highlight_pattern\n            self.dirty = True\n            return False\n'''
app = replace_once(app, old_highlight_open, new_highlight_open, "highlight prompt state reset")
write(app_path, app)


# ---------------------------------------------------------------------------
# Version, docs and tests
# ---------------------------------------------------------------------------
init_path = "src/htail_app/__init__.py"
init = read(init_path)
init = replace_once(init, 'VERSION = "0.12.0"', 'VERSION = "0.13.0"', "version bump")
write(init_path, init)

readme_path = "README.md"
readme = read(readme_path)
readme = replace_once(
    readme,
    '| `/` | Enter a regex search for the focused pane |',
    '| `/` | Open the focused pane\'s inline live search field |',
    "README search control",
)
readme = replace_once(
    readme,
    'Simple search is the default; `Tab` switches its prompt to explicit regex mode. `-I` / `--ignore-case` applies to both. Search matches use reverse video, while the currently selected `n` / `N` match gets a bright-yellow background and the pane title shows `MATCH x/y`. Persistent regex highlights use underline so existing syntax colors remain visible.',
    'Simple search is the default; `Tab` switches the inline field to explicit regex mode. `-I` / `--ignore-case` applies to both. Matches highlight live while you type. Non-selected matches use reverse video, while the currently selected `n` / `N` match uses guaranteed black-on-bright-yellow contrast. Match progress appears as a high-contrast badge on the pane\'s top-right border. Persistent regex highlights use underline so existing syntax colors remain visible.',
    "README search styling",
)
readme = replace_once(
    readme,
    'Press `/` for search inside the focused pane. Search opens in **Simple** mode: ordinary text is literal, `*` means any text and `?` means one character. Press `Tab` inside the search dialog to switch to explicit Python-regex mode. After applying a search, `n` / `N` move between matches.',
    'Press `/` for search inside the focused pane. A compact search field attaches to the bottom of that pane instead of opening a modal, so matching text remains visible and updates live while you type. Search opens in **Simple** mode: ordinary text is literal, `*` means any text and `?` means one character. Press `Tab` in the field to switch to explicit Python-regex mode, `Enter` to commit, or `Esc` to restore the previous search. After applying a search, `n` / `N` move between matches.',
    "README inline search paragraph",
)
write(readme_path, readme)

notes_path = "RELEASE_NOTES.md"
notes = '''# htail 0.13.0\n\n## New features\n\n- Local `/` search now uses an inline field attached to the bottom of the focused pane instead of a modal, keeping file content visible while searching.\n- Simple and Regex matches highlight live as the query is typed; `Tab` switches mode, `Enter` commits, and `Esc` restores the previous search.\n- Match progress now appears as a dedicated high-contrast badge on the pane's top-right border instead of competing with filename/follow/scroll state in the left title.\n\n## Bug fixes\n\n- Selected `n` / `N` matches now use guaranteed black-on-bright-yellow rendering so syntax-highlighted white text cannot produce an unreadable white/yellow combination.\n- Inline search reserves a real pane row, so opening the editor does not cover the final content line or invalidate EOF/scroll indicators.\n'''
write(notes_path, notes)

# Update the 0.12 regression expectations: MATCH moved from title() into the
# rendered top-right border badge.
test12_path = "tests/test_follow_search_012.py"
test12 = read(test12_path)
test12 = replace_once(
    test12,
    '        self.assertIn("MATCH 1/3", core.strip_ansi(pane.title(0, 100, True, 4)))\n',
    '        self.assertIn("MATCH 1/3", core.strip_ansi(pane.render_box(100, 6, True, 0)[0]))\n',
    "0.12 match 1 badge expectation",
)
test12 = replace_once(
    test12,
    '        self.assertIn("MATCH 2/3", core.strip_ansi(pane.title(0, 100, True, 4)))\n',
    '        self.assertIn("MATCH 2/3", core.strip_ansi(pane.render_box(100, 6, True, 0)[0]))\n',
    "0.12 match 2 badge expectation",
)
test12 = replace_once(
    test12,
    '        self.assertIn("MATCH 1/3", core.strip_ansi(pane.title(0, 100, True, 4)))\n',
    '        self.assertIn("MATCH 1/3", core.strip_ansi(pane.render_box(100, 6, True, 0)[0]))\n',
    "0.12 wrapped match badge expectation",
)
write(test12_path, test12)

new_tests = r'''from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from htail_app import app, core
from htail_app.app import MultiApp
from htail_app.pane import Pane
from htail_app.searching import SEARCH_REGEX, SEARCH_SIMPLE


class SearchContrastAndBadgeTests(unittest.TestCase):
    def make_pane(self):
        path = Path("s.txt")
        pane = Pane(path, core.SyntaxHighlighter(path, "none", True), core.DisplayFilter(), True, 0.0)
        rows = ["foo first\n", "middle\n", "foo second\n", "foo third\n"]
        pane.add_initial(rows)
        pane.set_snapshot(rows)
        pane.set_search("foo", mode=SEARCH_SIMPLE)
        pane.search_next(False, 60, 4)
        return pane

    def test_selected_match_is_guaranteed_black_on_bright_yellow(self):
        pane = self.make_pane()
        pane.render_box(60, 7, True, 0)
        selected = pane._snapshot_visual_lines[pane._snapshot_source_to_visual[pane._search_last_target]]
        self.assertIn("\x1b[1;30;103m", selected)
        self.assertIn("foo", core.strip_ansi(selected))

    def test_match_progress_is_a_top_right_border_badge(self):
        pane = self.make_pane()
        top = core.strip_ansi(pane.render_box(80, 7, True, 0)[0])
        self.assertIn("┤ MATCH 1/3 ├", top)
        self.assertGreater(top.index("MATCH 1/3"), 50)
        self.assertNotIn("MATCH", core.strip_ansi(pane.title(0, 80, True, 5)))


class InlineSearchTests(unittest.TestCase):
    def make_app(self, root: Path, *, color=False):
        source = root / "source.txt"
        source.write_text("alpha foo\nbeta\ngamma foo\ndelta\n", encoding="utf-8")
        args = app.parse_args([str(source), "--no-native-watch"] + (["--no-color"] if not color else []))
        return MultiApp(args, color, core.DisplayFilter(), core.UpdateService(""))

    def test_typing_updates_search_live_without_modal(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.handle_input("/")
                for ch in "foo":
                    application.handle_input(ch)
                pane = application.active_pane()
                self.assertEqual(pane.search_pattern, "foo")
                self.assertEqual(pane._search_match_total, 2)
                width, frame = application._frame_rows()
                plain = [core.strip_ansi(row) for row in frame]
                self.assertTrue(any("/ foo▌" in row for row in plain))
                self.assertFalse(any("Search · Simple" in row for row in plain))
                self.assertEqual(application.prompt_mode, "search")
            finally:
                application.close_native_watch()

    def test_escape_restores_previous_search(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                pane = application.active_pane()
                pane.set_search("alpha", mode=SEARCH_SIMPLE)
                pane.search_next(False, 60, 4)
                old_state = pane.search_state()
                application.handle_input("/")
                application.handle_input("\x7f")
                for ch in "foo":
                    application.handle_input(ch)
                self.assertEqual(pane.search_pattern, "alphfoo")
                application.handle_input("ESC")
                self.assertEqual(pane.search_state(), old_state)
                self.assertIsNone(application.prompt_mode)
            finally:
                application.close_native_watch()

    def test_invalid_regex_stays_inline_and_reports_error(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.handle_input("/")
                application.handle_input("TAB")
                application.handle_input("[")
                self.assertEqual(application.prompt_search_mode, SEARCH_REGEX)
                self.assertIsNotNone(application.prompt_error)
                application.handle_input("\r")
                self.assertEqual(application.prompt_mode, "search")
                _, frame = application._frame_rows()
                self.assertTrue(any("INVALID REGEX" in core.strip_ansi(row) for row in frame))
            finally:
                application.close_native_watch()

    def test_enter_commits_and_selects_first_match(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.handle_input("/")
                for ch in "foo":
                    application.handle_input(ch)
                application.handle_input("\r")
                pane = application.active_pane()
                self.assertIsNone(application.prompt_mode)
                self.assertEqual((pane._search_match_position, pane._search_match_total), (1, 2))
            finally:
                application.close_native_watch()

    def test_inline_search_reserves_one_real_pane_row(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                width, height, _ = application.content_dimensions()
                application._pane_boxes(width, height)
                rect = application.last_rects[0][1]
                application.handle_input("/")
                rows = application._pane_boxes(width, height)
                self.assertEqual(len(rows), height)
                local = [core.strip_ansi(row[rect.x:rect.x + rect.width]) for row in rows[rect.y:rect.y + rect.height]]
                self.assertTrue(any("/ ▌" in row for row in local))
            finally:
                application.close_native_watch()


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_search_013.py", new_tests)

print("htail 0.13.0 patch applied")
