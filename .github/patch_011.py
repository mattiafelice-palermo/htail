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


def sub_once(text, pattern, replacement, label):
    new, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return new


# ---------------------------------------------------------------------------
# Shared simple/regex search compiler + global result record.
# ---------------------------------------------------------------------------
write("src/htail_app/searching.py", r'''from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional, Pattern, Tuple

SEARCH_SIMPLE = "simple"
SEARCH_REGEX = "regex"
SEARCH_MODES = (SEARCH_SIMPLE, SEARCH_REGEX)


@dataclass(frozen=True)
class GlobalSearchMatch:
    pane_index: int
    source_index: int
    pane_name: str
    text: str
    match_start: int
    match_end: int


def simple_pattern_to_regex(expression: str) -> str:
    """Translate shell-like search wildcards while keeping all other text literal.

    `*` matches any text within one source line, `?` matches one character, and
    a backslash escapes the following character. Unlike fnmatch, the result is
    intentionally not anchored so ordinary text behaves as substring search.
    """
    out = []
    escaped = False
    for ch in expression:
        if escaped:
            out.append(re.escape(ch))
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    if escaped:
        out.append(re.escape("\\"))
    return "".join(out)


def compile_search(expression: str, mode: str, flags: int = 0) -> Tuple[Optional[Pattern[str]], Optional[str]]:
    if not expression:
        return None, None
    if mode == SEARCH_SIMPLE:
        source = simple_pattern_to_regex(expression)
    elif mode == SEARCH_REGEX:
        source = expression
    else:
        return None, f"unknown search mode: {mode}"
    try:
        return re.compile(source, flags), None
    except re.error as exc:
        return None, str(exc)


def search_label(expression: str, mode: str) -> str:
    return f"/{expression}/" if mode == SEARCH_REGEX else expression


def preview_around_match(text: str, start: int, end: int, limit: int) -> tuple[str, int, int]:
    """Return a compact preview plus match coordinates inside that preview."""
    text = text.rstrip("\r\n").replace("\t", "    ")
    limit = max(12, limit)
    if len(text) <= limit:
        return text, start, end
    center = (start + end) // 2
    left = max(0, min(center - limit // 2, len(text) - limit))
    right = min(len(text), left + limit)
    preview = text[left:right]
    pstart = max(0, start - left)
    pend = max(pstart, min(len(preview), end - left))
    if left > 0:
        preview = "…" + preview[1:]
        pstart = max(0, pstart)
        pend = max(pstart, pend)
    if right < len(text):
        preview = preview[:-1] + "…"
    return preview, pstart, pend
''')


# ---------------------------------------------------------------------------
# Pane: preserve regex highlight, add Simple/Regex search state and direct jump.
# ---------------------------------------------------------------------------
path = "src/htail_app/pane.py"
text = read(path)
text = replace_once(
    text,
    "from . import core\n",
    "from . import core\nfrom .searching import SEARCH_REGEX, SEARCH_SIMPLE, compile_search, search_label\n",
    "pane search import",
)
text = replace_once(
    text,
    '''        self.search_pattern = ""\n        self.search_regex: Optional[Pattern[str]] = None\n        self._search_last_target: Optional[int] = None\n''',
    '''        self.search_pattern = ""\n        self.search_mode = SEARCH_SIMPLE\n        self.search_regex: Optional[Pattern[str]] = None\n        self._search_last_target: Optional[int] = None\n''',
    "pane search state",
)
text = sub_once(
    text,
    r'''    def set_search\(self, expression: str, flags: int = 0\) -> Optional\[str\]:\n.*?\n    def set_highlight''',
    '''    def set_search(self, expression: str, flags: int = 0, mode: str = SEARCH_REGEX) -> Optional[str]:\n        if not expression:\n            self.search_pattern = ""\n            self.search_mode = mode\n            self.search_regex = None\n            self._search_last_target = None\n            self._mark_layout_dirty()\n            self._snapshot_layout_dirty = True\n            return None\n        compiled, error = compile_search(expression, mode, flags)\n        if error is not None:\n            return error\n        self.search_pattern = expression\n        self.search_mode = mode\n        self.search_regex = compiled\n        self._search_last_target = None\n        self._mark_layout_dirty()\n        self._snapshot_layout_dirty = True\n        return None\n\n    def _search_display(self) -> str:\n        return search_label(self.search_pattern, self.search_mode)\n\n    def set_highlight''',
    "replace pane set_search",
)
text = replace_once(
    text,
    '''    def search_next(self, reverse: bool, width: int, body_height: int) -> bool:\n''',
    '''    def jump_to_source_line(self, source_index: int, width: int, body_height: int) -> bool:\n        """Show a current-snapshot source line, centered when geometry allows."""\n        if not self.snapshot_raw:\n            return False\n        self.prefer_snapshot = True\n        self._snapshot_anchor_pending = False\n        self._ensure_snapshot_layout(max(1, width))\n        visual = self._snapshot_source_to_visual.get(source_index)\n        if visual is None:\n            return False\n        body_height = max(1, body_height)\n        desired = max(0, visual - body_height // 2)\n        self._snapshot_top = min(desired, self._snapshot_max_top(body_height))\n        self._search_last_target = source_index\n        return True\n\n    def search_next(self, reverse: bool, width: int, body_height: int) -> bool:\n''',
    "insert pane direct jump",
)
text = text.replace('self.set_message(f"no match: /{self.search_pattern}/")', 'self.set_message(f"no match: {self._search_display()}")')
text = text.replace('self.set_message(f"match {position}/{len(candidates)}: /{self.search_pattern}/")', 'self.set_message(f"match {position}/{len(candidates)}: {self._search_display()}")')
write(path, text)


# ---------------------------------------------------------------------------
# App: local Simple/Regex modal and global live-search palette.
# ---------------------------------------------------------------------------
path = "src/htail_app/app.py"
text = read(path)
text = replace_once(
    text,
    "from .pane import Pane, StreamPane\n",
    "from .pane import Pane, StreamPane\nfrom .searching import GlobalSearchMatch, SEARCH_REGEX, SEARCH_SIMPLE, compile_search, preview_around_match, search_label\n",
    "app search import",
)
text = replace_once(
    text,
    "MIN_UPDATE_COMPLETE_SECONDS = 0.35\n",
    "MIN_UPDATE_COMPLETE_SECONDS = 0.35\nGLOBAL_SEARCH_LIMIT = 250\n",
    "global search limit",
)
text = replace_once(
    text,
    '''        self.prompt_mode: Optional[str] = None\n        self.prompt_buffer = ""\n        self._last_frame: Optional[List[str]] = None\n''',
    '''        self.prompt_mode: Optional[str] = None\n        self.prompt_buffer = ""\n        self.prompt_search_mode = SEARCH_SIMPLE\n        self.global_search_active = False\n        self.global_search_buffer = ""\n        self.global_search_mode = SEARCH_SIMPLE\n        self.global_search_results: List[GlobalSearchMatch] = []\n        self.global_search_selected = 0\n        self.global_search_error: Optional[str] = None\n        self.global_search_truncated = False\n        self._last_frame: Optional[List[str]] = None\n''',
    "app search state",
)
text = sub_once(
    text,
    r'''    def _prompt_lines\(self, width: int, height: int\) -> List\[str\]:\n.*?\n    def _active_pane_geometry''',
    r'''    def _search_flags(self) -> int:
        return re.IGNORECASE if self.args.ignore_case else 0

    @staticmethod
    def _other_search_mode(mode: str) -> str:
        return SEARCH_REGEX if mode == SEARCH_SIMPLE else SEARCH_SIMPLE

    @staticmethod
    def _search_mode_name(mode: str) -> str:
        return "Simple" if mode == SEARCH_SIMPLE else "Regex"

    def _prompt_lines(self, width: int, height: int) -> List[str]:
        mode = self.prompt_mode or "search"
        if mode == "search":
            mode_name = self._search_mode_name(self.prompt_search_mode)
            title = f"Search · {mode_name}"
            content = [
                core.paint("/ " + self.prompt_buffer, core.BOLD_LIGHT_CYAN, self.color),
                "",
                f"Mode: {mode_name} · Tab toggles Simple / Regex",
            ]
            if self.prompt_search_mode == SEARCH_SIMPLE:
                content.append("Simple: ordinary text is literal · * any text · ? one character")
            else:
                content.append("Regex: Python regular-expression syntax")
            content.extend(["", "Enter apply · Esc cancel · Backspace edit"])
            return _panel_lines(title, content, width, height, self.color)

        content = [
            core.paint("highlight: " + self.prompt_buffer, core.BOLD_LIGHT_CYAN, self.color),
            "",
            "Regex highlight · Enter apply · Esc cancel · Backspace edit",
            "Use H from the viewer to clear the active highlight.",
        ]
        return _panel_lines("Regex highlight", content, width, height, self.color)

    def _refresh_global_search_results(self) -> None:
        pattern, error = compile_search(self.global_search_buffer, self.global_search_mode, self._search_flags())
        self.global_search_error = error
        self.global_search_truncated = False
        if pattern is None:
            self.global_search_results = []
            self.global_search_selected = 0
            return

        results: List[GlobalSearchMatch] = []
        for pane_index, pane in enumerate(self.panes):
            for source_index, raw in enumerate(pane.snapshot_raw):
                if not pane.display_filter.accepts(raw):
                    continue
                plain = raw.rstrip("\r\n")
                match = pattern.search(plain)
                if match is None:
                    continue
                results.append(GlobalSearchMatch(
                    pane_index,
                    source_index,
                    pane.name,
                    plain,
                    match.start(),
                    match.end(),
                ))
                if len(results) >= GLOBAL_SEARCH_LIMIT:
                    self.global_search_truncated = True
                    break
            if self.global_search_truncated:
                break
        self.global_search_results = results
        if results:
            self.global_search_selected = min(max(0, self.global_search_selected), len(results) - 1)
        else:
            self.global_search_selected = 0

    def _global_search_lines(self, width: int, height: int) -> List[str]:
        self._refresh_global_search_results()
        mode_name = self._search_mode_name(self.global_search_mode)
        count = len(self.global_search_results)
        count_label = f"{count}+ matches" if self.global_search_truncated else f"{count} match{'es' if count != 1 else ''}"
        content: List[str] = [
            core.paint("> " + self.global_search_buffer, core.BOLD_LIGHT_CYAN, self.color),
            f"Mode: {mode_name} · Tab toggles Simple / Regex · {count_label}",
        ]
        if self.global_search_mode == SEARCH_SIMPLE:
            content.append("Simple: literal text · * any text · ? one character")
        else:
            content.append("Regex: Python regular-expression syntax")
        content.append("")

        if self.global_search_error:
            content.append(core.paint(f"Invalid regex: {self.global_search_error}", core.BOLD_YELLOW, self.color))
        elif not self.global_search_buffer:
            content.append(core.paint("Type to search every currently watched file.", core.DIM, self.color))
        elif not self.global_search_results:
            content.append(core.paint("No matches.", core.DIM, self.color))
        else:
            visible_slots = max(3, min(12, height - 10))
            selected = self.global_search_selected
            start = max(0, selected - visible_slots // 2)
            start = min(start, max(0, len(self.global_search_results) - visible_slots))
            end = min(len(self.global_search_results), start + visible_slots)
            preview_limit = max(18, min(80, width - 34))
            for index in range(start, end):
                result = self.global_search_results[index]
                preview, pstart, pend = preview_around_match(
                    result.text, result.match_start, result.match_end, preview_limit
                )
                if self.color and pend > pstart:
                    preview = preview[:pstart] + "\x1b[7m" + preview[pstart:pend] + "\x1b[27m" + preview[pend:]
                prefix = "▶" if index == selected else " "
                label = f"[{result.pane_index + 1}] {result.pane_name}:{result.source_index + 1}"
                row = f"{prefix} {label}  {preview}"
                if index == selected:
                    row = core.paint(row, core.BOLD_LIGHT_CYAN, self.color)
                content.append(row)
            if self.global_search_truncated:
                content.append(core.paint(f"Showing first {GLOBAL_SEARCH_LIMIT} matches.", core.DIM, self.color))

        content.extend(["", "↑/↓ select · Enter jump · Tab mode · Esc close"])
        return _panel_lines("Global search", content, width, height, self.color)

    def _select_global_search_result(self) -> bool:
        self._refresh_global_search_results()
        if not self.global_search_results:
            return False
        result = self.global_search_results[self.global_search_selected]
        if result.pane_index >= len(self.panes):
            return False
        if self.layout == "stream":
            self.layout = "auto"
            self.maximized = False
        self.focus = result.pane_index
        pane = self.panes[result.pane_index]
        error = pane.set_search(
            self.global_search_buffer,
            self._search_flags(),
            mode=self.global_search_mode,
        )
        if error is not None:
            self.global_search_error = error
            return False
        inner_w, body_h = self._active_pane_geometry()
        pane.jump_to_source_line(result.source_index, inner_w, body_h)
        pane.set_message(
            f"global match {result.source_index + 1}: {search_label(self.global_search_buffer, self.global_search_mode)}",
            4.0,
        )
        self.global_search_active = False
        self.global_search_error = None
        self.dirty = True
        return True

    def _active_pane_geometry''',
    "replace prompt/global search methods",
)
text = text.replace(
    '            "  /                  regex search; n / N next / previous match",\n',
    '            "  /                  search focused pane; Tab toggles Simple / Regex",\n            "  n / N              next / previous local match",\n'
)
text = text.replace(
    '            "Global",\n            "  u                  check/install updates",\n',
    '            "Global",\n            "  g                  live search across all watched files",\n            "  u                  check/install updates",\n'
)
text = text.replace(
    '        controls = "/ search · n/N match · h highlight · Tab pane · l layout · ↑↓/Pg scroll · [/] update · f newest · p pause · u update · q quit · ? help"\n',
    '        controls = "/ search · g global · n/N match · h highlight · Tab pane · l layout · ↑↓/Pg scroll · [/] update · f newest · p pause · u update · q quit · ? help"\n'
)
text = replace_once(
    text,
    '''        if self.prompt_mode:\n            body = _overlay_modal(base_body, self._prompt_lines(width, body_height), width, body_height, self.color)\n''',
    '''        if self.global_search_active:\n            body = _overlay_modal(base_body, self._global_search_lines(width, body_height), width, body_height, self.color)\n        elif self.prompt_mode:\n            body = _overlay_modal(base_body, self._prompt_lines(width, body_height), width, body_height, self.color)\n''',
    "global search modal rendering",
)
text = replace_once(
    text,
    '''        if self.prompt_mode:\n            status = ["REGEX · Enter apply · Esc cancel", "Background watching continues while this dialog is open"]\n''',
    '''        if self.global_search_active:\n            status = [f"GLOBAL SEARCH · {self._search_mode_name(self.global_search_mode)} · ↑↓ select · Enter jump · Tab mode · Esc close", "Background watching continues while this dialog is open"]\n        elif self.prompt_mode:\n            if self.prompt_mode == "search":\n                status = [f"SEARCH · {self._search_mode_name(self.prompt_search_mode)} · Tab mode · Enter apply · Esc cancel", "Background watching continues while this dialog is open"]\n            else:\n                status = ["REGEX HIGHLIGHT · Enter apply · Esc cancel", "Background watching continues while this dialog is open"]\n''',
    "search modal footer",
)

# Input: global palette has priority; local search gets Tab mode toggle.
marker = '''    def handle_input(self, event: InputEvent) -> bool:\n        if self.prompt_mode and not isinstance(event, MouseEvent):\n'''
replacement = '''    def handle_input(self, event: InputEvent) -> bool:\n        if self.global_search_active and not isinstance(event, MouseEvent):\n            key = event\n            if key == "ESC":\n                self.global_search_active = False\n                self.global_search_error = None\n                self.dirty = True\n                return False\n            if key in ("TAB", "SHIFT_TAB"):\n                self.global_search_mode = self._other_search_mode(self.global_search_mode)\n                self.global_search_selected = 0\n                self._refresh_global_search_results()\n                self.dirty = True\n                return False\n            if key in ("UP", "DOWN", "PAGEUP", "PAGEDOWN"):\n                self._refresh_global_search_results()\n                if self.global_search_results:\n                    delta = {"UP": -1, "DOWN": 1, "PAGEUP": -8, "PAGEDOWN": 8}[key]\n                    self.global_search_selected = min(\n                        max(0, self.global_search_selected + delta),\n                        len(self.global_search_results) - 1,\n                    )\n                self.dirty = True\n                return False\n            if key in ("\\r", "\\n"):\n                self._select_global_search_result()\n                self.dirty = True\n                return False\n            if key in ("\\x7f", "\\b"):\n                self.global_search_buffer = self.global_search_buffer[:-1]\n                self.global_search_selected = 0\n                self._refresh_global_search_results()\n                self.dirty = True\n                return False\n            if isinstance(key, str) and len(key) == 1 and key.isprintable():\n                self.global_search_buffer += key\n                self.global_search_selected = 0\n                self._refresh_global_search_results()\n                self.dirty = True\n            return False\n\n        if self.prompt_mode and not isinstance(event, MouseEvent):\n'''
text = replace_once(text, marker, replacement, "global search input priority")
text = replace_once(
    text,
    '''            if key == "ESC":\n                self.prompt_mode = None\n                self.prompt_buffer = ""\n                self.dirty = True\n                return False\n            if key in ("\\r", "\\n"):\n''',
    '''            if key == "ESC":\n                self.prompt_mode = None\n                self.prompt_buffer = ""\n                self.dirty = True\n                return False\n            if key in ("TAB", "SHIFT_TAB") and self.prompt_mode == "search":\n                self.prompt_search_mode = self._other_search_mode(self.prompt_search_mode)\n                self.dirty = True\n                return False\n            if key in ("\\r", "\\n"):\n''',
    "local search mode toggle",
)
text = replace_once(
    text,
    '''                flags = re.IGNORECASE if self.args.ignore_case else 0\n                if self.prompt_mode == "search":\n                    error = pane.set_search(self.prompt_buffer, flags)\n''',
    '''                flags = self._search_flags()\n                if self.prompt_mode == "search":\n                    error = pane.set_search(self.prompt_buffer, flags, mode=self.prompt_search_mode)\n''',
    "local search compile mode",
)
text = text.replace(
    '                    self.set_message(f"invalid regex: {error}", 5.0)\n',
    '                    self.set_message(f"invalid search: {error}", 5.0)\n',
    1,
)
text = replace_once(
    text,
    '''        if isinstance(event, MouseEvent):\n            if not (self.help_active or self.layout_menu or self.update_confirm_active):\n                self.handle_mouse(event)\n''',
    '''        if isinstance(event, MouseEvent):\n            if not (self.help_active or self.layout_menu or self.update_confirm_active or self.global_search_active or self.prompt_mode):\n                self.handle_mouse(event)\n''',
    "block background mouse under search modals",
)
text = replace_once(
    text,
    '''        if key == "/":\n            self.prompt_mode = "search"\n            self.prompt_buffer = self.active_pane().search_pattern\n            self.dirty = True\n            return False\n        if key == "h":\n''',
    '''        if key == "/":\n            pane = self.active_pane()\n            self.prompt_mode = "search"\n            self.prompt_buffer = pane.search_pattern\n            self.prompt_search_mode = pane.search_mode if pane.search_pattern else SEARCH_SIMPLE\n            self.dirty = True\n            return False\n        if key in ("g", "G"):\n            self.global_search_active = True\n            self.global_search_selected = 0\n            self._refresh_global_search_results()\n            self.dirty = True\n            return False\n        if key == "h":\n''',
    "open local/global search",
)
write(path, text)


# ---------------------------------------------------------------------------
# Version, release notes, README and backlog.
# ---------------------------------------------------------------------------
path = "src/htail_app/__init__.py"
text = read(path).replace('VERSION = "0.10.0"', 'VERSION = "0.11.0"')
write(path, text)

write("RELEASE_NOTES.md", '''# htail 0.11.0\n\n## New features\n\n- Local `/` search now opens in **Simple** mode by default: ordinary characters are literal, `*` matches any text, and `?` matches one character. Press `Tab` inside the modal to switch explicitly between Simple and Regex modes.\n- Added `g` global live search across all currently watched files. Results update while typing and show pane, source line and a matching preview.\n- In global search, use `↑` / `↓` to choose a result and `Enter` to focus that pane, jump to the matching source line and make the query the pane's active search so `n` / `N` continue naturally.\n- Global search also supports the same explicit Simple / Regex toggle with `Tab`.\n\n## Bug fixes / safety\n\n- Regex punctuation is treated literally in Simple mode, so searches such as `a.b`, `[045]` or paths do not require escaping.\n- Search modals now ignore mouse clicks on the dimmed background instead of changing pane focus behind the dialog.\n- Existing `h` persistent highlight rules remain explicitly regex-based; their behavior is unchanged.\n''')

path = "README.md"
text = read(path)
text = text.replace(
    '| `/` | regex search; `n` / `N` next / previous match |',
    '| `/` | search focused pane; Simple mode by default, `Tab` toggles Simple / Regex |'
)
text = text.replace(
    '| `h` | set regex highlight; `H` clears it |',
    '| `g` | global live search across all watched files |\n| `n` / `N` | next / previous local search match |\n| `h` | set regex highlight; `H` clears it |'
)
needle = 'Mouse tracking can be disabled with `--no-mouse`. Keyboard controls always remain available.\n\n'
addition = '''Mouse tracking can be disabled with `--no-mouse`. Keyboard controls always remain available.\n\n### Search\n\nPress `/` for search inside the focused pane. Search opens in **Simple** mode: ordinary text is literal, `*` means any text and `?` means one character. Press `Tab` inside the search dialog to switch to explicit Python-regex mode. After applying a search, `n` / `N` move between matches.\n\nExamples:\n\n```text\n045blabla       literal substring\n045*blabla      045, then any text, then blabla\nrun-??-error    exactly two characters between the dashes\n```\n\nPress `g` for **global live search** across every currently watched file. Results update as you type. Use `↑` / `↓` to choose a result and `Enter` to focus its pane and jump to the matching source line; the query becomes that pane's active local search. `Tab` toggles Simple / Regex here as well.\n\n'''
if needle not in text:
    raise RuntimeError("README search insertion marker missing")
text = text.replace(needle, addition, 1)
write(path, text)

path = "docs/NEXT.md"
text = read(path)
text = text.replace(
    '- **Global live search palette** — open a modal that searches across all currently watched files while the user types, showing source/pane plus a short matching-line preview. Selecting a result should close the modal, focus the corresponding pane, jump to that match, and temporarily highlight the matched text. The interaction should support an easy literal/wildcard mode as well as explicit regex mode rather than requiring regex syntax for ordinary searches.\n',
    '- **Global-search refinements** — 0.11.0 adds the live cross-file palette with Simple/Regex modes and jump-to-result. Future refinements could add mouse result selection, optional surrounding context, match grouping by file, or a compact result minimap.\n'
)
text = text.replace(
    'Interactive regex search, regex highlighting and live glob discovery have now landed in htail;',
    'Simple/regex local search, global live search, regex highlighting and live glob discovery have now landed in htail;'
)
write(path, text)


# ---------------------------------------------------------------------------
# Regression tests.
# ---------------------------------------------------------------------------
write("tests/test_search_011.py", r'''from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from htail_app import app, core
from htail_app.app import MultiApp
from htail_app.pane import Pane
from htail_app.searching import SEARCH_REGEX, SEARCH_SIMPLE, compile_search, simple_pattern_to_regex


class SimpleSearchCompilerTests(unittest.TestCase):
    def test_simple_search_is_literal_except_star_and_question(self):
        pattern, error = compile_search("a.b[1]", SEARCH_SIMPLE)
        self.assertIsNone(error)
        self.assertIsNotNone(pattern.search("prefix a.b[1] suffix"))
        self.assertIsNone(pattern.search("prefix axb1 suffix"))

        pattern, error = compile_search("045*blabla", SEARCH_SIMPLE)
        self.assertIsNone(error)
        self.assertIsNotNone(pattern.search("045 anything in between blabla"))
        self.assertIsNone(pattern.search("044 anything blabla"))

        pattern, _ = compile_search("run-??-error", SEARCH_SIMPLE)
        self.assertIsNotNone(pattern.search("run-ab-error"))
        self.assertIsNone(pattern.search("run-a-error"))

    def test_simple_backslash_escapes_wildcards(self):
        pattern, error = compile_search(r"file\*name", SEARCH_SIMPLE)
        self.assertIsNone(error)
        self.assertIsNotNone(pattern.search("file*name"))
        self.assertIsNone(pattern.search("file-long-name"))

    def test_explicit_regex_retains_regex_semantics(self):
        pattern, error = compile_search(r"045.*blabla", SEARCH_REGEX)
        self.assertIsNone(error)
        self.assertIsNotNone(pattern.search("045 xyz blabla"))
        _, error = compile_search("[", SEARCH_REGEX)
        self.assertIsNotNone(error)


class PaneSearchModeTests(unittest.TestCase):
    def make_pane(self):
        highlighter = core.SyntaxHighlighter(Path("x.txt"), "none", False)
        pane = Pane(Path("x.txt"), highlighter, core.DisplayFilter(), False, 0.0)
        rows = ["a.b literal\n", "axb regex-like\n", "045 xyz blabla\n", "tail\n"]
        pane.add_initial(rows)
        pane.set_snapshot(rows)
        return pane

    def test_simple_mode_and_regex_mode_are_distinct(self):
        pane = self.make_pane()
        self.assertIsNone(pane.set_search("a.b", mode=SEARCH_SIMPLE))
        self.assertEqual(pane.search_mode, SEARCH_SIMPLE)
        self.assertTrue(pane.search_next(False, 40, 3))
        self.assertEqual(pane._search_last_target, 0)

        self.assertIsNone(pane.set_search("a.b", mode=SEARCH_REGEX))
        self.assertTrue(pane.search_next(False, 40, 3))
        self.assertEqual(pane._search_last_target, 0)
        self.assertTrue(pane.search_next(False, 40, 3))
        self.assertEqual(pane._search_last_target, 1)

    def test_jump_to_source_line_centers_current_snapshot(self):
        pane = self.make_pane()
        pane.set_search("045*blabla", mode=SEARCH_SIMPLE)
        self.assertTrue(pane.jump_to_source_line(2, 40, 3))
        self.assertTrue(pane.prefer_snapshot)
        self.assertEqual(pane._search_last_target, 2)


class SearchModalInteractionTests(unittest.TestCase):
    def make_app(self, root: Path):
        a = root / "a.txt"
        b = root / "b.txt"
        a.write_text("alpha\na.b literal\nomega\n", encoding="utf-8")
        b.write_text("first\n045 something blabla\nlast\n", encoding="utf-8")
        args = app.parse_args([str(a), str(b), "--no-native-watch", "--no-color"])
        return MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))

    def test_local_search_defaults_simple_and_tab_toggles_regex(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.handle_input("/")
                self.assertEqual(application.prompt_search_mode, SEARCH_SIMPLE)
                application.handle_input("TAB")
                self.assertEqual(application.prompt_search_mode, SEARCH_REGEX)
                application.handle_input("TAB")
                self.assertEqual(application.prompt_search_mode, SEARCH_SIMPLE)
            finally:
                application.close_native_watch()

    def test_global_search_is_live_and_enter_focuses_matching_pane(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.handle_input("g")
                for ch in "045*blabla":
                    application.handle_input(ch)
                self.assertTrue(application.global_search_active)
                self.assertEqual(len(application.global_search_results), 1)
                result = application.global_search_results[0]
                self.assertEqual(result.pane_index, 1)
                self.assertEqual(result.source_index, 1)

                application.handle_input("\r")
                self.assertFalse(application.global_search_active)
                self.assertEqual(application.focus, 1)
                pane = application.panes[1]
                self.assertEqual(pane.search_pattern, "045*blabla")
                self.assertEqual(pane.search_mode, SEARCH_SIMPLE)
                self.assertEqual(pane._search_last_target, 1)
            finally:
                application.close_native_watch()

    def test_global_tab_switches_to_regex_and_invalid_regex_stays_open(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.handle_input("g")
                application.handle_input("TAB")
                self.assertEqual(application.global_search_mode, SEARCH_REGEX)
                application.handle_input("[")
                self.assertTrue(application.global_search_active)
                self.assertIsNotNone(application.global_search_error)
                self.assertEqual(application.global_search_results, [])
            finally:
                application.close_native_watch()

    def test_selecting_global_result_from_stream_layout_returns_to_file_layout(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.layout = "stream"
                application.global_search_active = True
                application.global_search_buffer = "045*blabla"
                application._refresh_global_search_results()
                self.assertTrue(application._select_global_search_result())
                self.assertEqual(application.layout, "auto")
                self.assertEqual(application.focus, 1)
            finally:
                application.close_native_watch()


if __name__ == "__main__":
    unittest.main()
''')

print("htail 0.11.0 search patch applied")
