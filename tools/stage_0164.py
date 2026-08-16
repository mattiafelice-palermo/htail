from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing replacement anchor in {path}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: str, start: str, end: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    i = text.index(start)
    j = text.index(end, i)
    p.write_text(text[:i] + new + text[j:], encoding="utf-8")


# ---------------------------------------------------------------------------
# Input: decode Shift+Up / Shift+Down on POSIX terminals.
# ---------------------------------------------------------------------------
replace_once(
    "src/htail_app/input.py",
    '        "\\x1bOH": "HOME", "\\x1bOF": "END", "\\x1b[Z": "SHIFT_TAB",\n        "\\x1b": "ESC",\n',
    '        "\\x1bOH": "HOME", "\\x1bOF": "END", "\\x1b[Z": "SHIFT_TAB",\n        "\\x1b[1;2A": "SHIFT_UP", "\\x1b[1;2B": "SHIFT_DOWN",\n        "\\x1b": "ESC",\n',
)

# ---------------------------------------------------------------------------
# Renderer: labelled fuzzy columns + real explicit grouped expansion + hit map.
# ---------------------------------------------------------------------------
new_result_helpers = r'''def _flat_column_widths(width: int) -> Tuple[int, int, int, int]:
    filename_width = max(12, min(28, width // 4))
    line_width = 6
    score_width = 5
    fixed = 2 + 3 + 2 + filename_width + 1 + line_width + 2 + 2 + score_width
    preview_width = max(8, width - fixed)
    return filename_width, line_width, preview_width, score_width


def _flat_result_header(width: int) -> str:
    filename_width, line_width, preview_width, score_width = _flat_column_widths(width)
    row = (
        f"  {'#':>3}  {'FILE':<{filename_width}.{filename_width}} "
        f"{'LINE':>{line_width}}  {'MATCH':<{preview_width}.{preview_width}}  {'SCORE':>{score_width}}"
    )
    return _pad(row, width)


def _flat_result_rows(
    results: Sequence[GlobalSearchMatch], selected: int, rows: int, width: int, color: bool
) -> Tuple[List[str], List[Tuple[str, int]]]:
    if not results:
        return [], []
    start = max(0, selected - rows // 2)
    start = min(start, max(0, len(results) - rows))
    out: List[str] = []
    tags: List[Tuple[str, int]] = []
    filename_width, line_width, preview_width, score_width = _flat_column_widths(width)
    for index in range(start, min(len(results), start + rows)):
        result = results[index]
        selected_row = index == selected
        preview = result.text
        if len(preview) > preview_width:
            left = max(0, min(result.match_start - preview_width // 3, len(preview) - preview_width))
            right = min(len(preview), left + preview_width)
            local_start = max(0, result.match_start - left)
            local_end = max(local_start, min(right - left, result.match_end - left))
            preview = preview[left:right]
            if left > 0 and preview:
                preview = "…" + preview[1:]
            if right < len(result.text) and preview:
                preview = preview[:-1] + "…"
        else:
            local_start, local_end = result.match_start, result.match_end
        preview = _highlight_span(preview, local_start, local_end, selected=selected_row, color=color)
        score = f"{result.score:>{score_width}.0f}" if result.score is not None else " " * score_width
        marker = "▌" if selected_row else " "
        row = (
            f"{marker} {index + 1:>3}  {result.pane_name:<{filename_width}.{filename_width}} "
            f"{result.source_index + 1:>{line_width}}  {_pad(preview, preview_width)}  {score}"
        )
        if selected_row and color:
            row = "\x1b[1;97;48;5;24m" + row + core.RESET
        out.append(_pad(row, width))
        tags.append(("result", index))
    return out, tags


def _grouped_result_rows(
    results: Sequence[GlobalSearchMatch],
    selected: int,
    rows: int,
    width: int,
    color: bool,
    expanded_pane: Optional[int],
) -> Tuple[List[str], List[Tuple[str, int]]]:
    if not results:
        return [], []
    groups: List[Tuple[int, str, List[Tuple[int, GlobalSearchMatch]]]] = []
    group_map = {}
    for index, result in enumerate(results):
        if result.pane_index not in group_map:
            group_map[result.pane_index] = len(groups)
            groups.append((result.pane_index, result.pane_name, []))
        groups[group_map[result.pane_index]][2].append((index, result))
    selected_pane = results[selected].pane_index
    header_count = min(len(groups), rows)
    active_slots = max(1, rows - header_count)
    out: List[str] = []
    tags: List[Tuple[str, int]] = []
    for pane_index, pane_name, members in groups:
        expanded = pane_index == expanded_pane
        selected_group = pane_index == selected_pane
        symbol = "▼" if expanded else "▶"
        best = max((member.score or 0.0) for _, member in members)
        score_suffix = f" · best {best:.0f}" if members and members[0][1].score is not None else ""
        header = f"{symbol} {pane_name}  {len(members)}{score_suffix}"
        style = core.BOLD_LIGHT_CYAN if selected_group else core.DIM
        out.append(_pad(core.paint(header, style, color), width))
        tags.append(("file", pane_index))
        if len(out) >= rows:
            break
        if not expanded:
            continue
        selected_member_pos = next((i for i, (global_index, _) in enumerate(members) if global_index == selected), 0)
        member_start = max(0, selected_member_pos - active_slots // 2)
        member_start = min(member_start, max(0, len(members) - active_slots))
        for global_index, result in members[member_start:member_start + active_slots]:
            selected_row = global_index == selected
            marker = "▌" if selected_row else " "
            prefix = f"{marker} {result.source_index + 1:>6}  "
            room = max(8, width - len(prefix) - 8)
            preview = result.text
            local_start, local_end = result.match_start, result.match_end
            if len(preview) > room:
                left = max(0, min(result.match_start - room // 3, len(preview) - room))
                right = min(len(preview), left + room)
                local_start = max(0, result.match_start - left)
                local_end = max(local_start, min(right - left, result.match_end - left))
                preview = preview[left:right]
                if left > 0 and preview:
                    preview = "…" + preview[1:]
                if right < len(result.text) and preview:
                    preview = preview[:-1] + "…"
            preview = _highlight_span(preview, local_start, local_end, selected=selected_row, color=color)
            score = f"  {result.score:.0f}" if result.score is not None else ""
            row = prefix + preview + score
            if selected_row and color:
                row = "\x1b[1;97;48;5;24m" + row + core.RESET
            out.append(_pad(row, width))
            tags.append(("result", global_index))
            if len(out) >= rows:
                break
        if len(out) >= rows:
            break
    return out[:rows], tags[:rows]


'''
replace_between(
    "src/htail_app/global_search.py",
    "def _flat_result_rows(\n",
    "def _preview_rows(\n",
    new_result_helpers,
)

replace_once(
    "src/htail_app/global_search.py",
    '    preview_enabled: bool,\n    color: bool,\n) -> List[str]:\n',
    '    preview_enabled: bool,\n    color: bool,\n    expanded_pane: Optional[int] = None,\n    hit_regions: Optional[List[Tuple[int, int, int, int, str, int]]] = None,\n) -> List[str]:\n',
)

replace_once(
    "src/htail_app/global_search.py",
    '    footer_text = "↑↓ select · Enter jump · Tab mode · Ctrl+T case · Ctrl+O sort · Ctrl+F file · Ctrl+P preview · Esc close"\n',
    '    footer_text = "↑↓ match · Shift+↑↓ file · Enter jump · Tab mode · Ctrl+T case · Ctrl+O(letter) sort · Ctrl+F file · Ctrl+P preview · Esc close"\n',
)

old_rows = '''    selected_result = results[selected] if results and 0 <= selected < len(results) else None
    if error:
        left_rows = [core.paint(f"Invalid search: {error}", core.BOLD_YELLOW, color)]
    elif not query:
        left_rows = [core.paint("Type to search every currently watched file.", core.DIM, color)]
    elif not results:
        left_rows = [core.paint("No matches.", core.DIM, color)]
    elif sort_mode == SORT_RELEVANCE:
        left_rows = _flat_result_rows(results, selected, body_height - 1, left_width, color)
    else:
        left_rows = _grouped_result_rows(results, selected, body_height - 1, left_width, color)
    left_heading = "RESULTS — best matches" if sort_mode == SORT_RELEVANCE else "RESULTS — grouped by file"
    left_rows = [core.paint(left_heading, core.BOLD_LIGHT_CYAN, color)] + left_rows
    left_rows = left_rows[:body_height] + [""] * max(0, body_height - len(left_rows))
'''
new_rows = '''    selected_result = results[selected] if results and 0 <= selected < len(results) else None
    left_tags: List[Optional[Tuple[str, int]]] = []
    if error:
        left_rows = [core.paint(f"Invalid search: {error}", core.BOLD_YELLOW, color)]
        left_tags = [None]
    elif not query:
        left_rows = [core.paint("Type to search every currently watched file.", core.DIM, color)]
        left_tags = [None]
    elif not results:
        left_rows = [core.paint("No matches.", core.DIM, color)]
        left_tags = [None]
    elif sort_mode == SORT_RELEVANCE:
        result_rows, result_tags = _flat_result_rows(results, selected, max(1, body_height - 2), left_width, color)
        left_rows = [core.paint(_flat_result_header(left_width), core.DIM, color)] + result_rows
        left_tags = [None] + result_tags
    else:
        left_rows, grouped_tags = _grouped_result_rows(
            results, selected, max(1, body_height - 1), left_width, color, expanded_pane
        )
        left_tags = list(grouped_tags)
    left_heading = "RESULTS — best matches" if sort_mode == SORT_RELEVANCE else "RESULTS — grouped by file"
    left_rows = [core.paint(left_heading, core.BOLD_LIGHT_CYAN, color)] + left_rows
    left_tags = [None] + left_tags
    left_rows = left_rows[:body_height] + [""] * max(0, body_height - len(left_rows))
    left_tags = left_tags[:body_height] + [None] * max(0, body_height - len(left_tags))

    if hit_regions is not None:
        hit_regions.clear()
        body_y = top_margin + 5
        content_x1 = left_margin + 1
        content_x2 = content_x1 + left_width
        for offset, tag in enumerate(left_tags):
            if tag is None:
                continue
            kind, value = tag
            hit_regions.append((content_x1, body_y + offset, content_x2, body_y + offset + 1, kind, value))
'''
replace_once("src/htail_app/global_search.py", old_rows, new_rows)

# ---------------------------------------------------------------------------
# App state/navigation/mouse interactions and clearer Ctrl+O wording.
# ---------------------------------------------------------------------------
replace_once(
    "src/htail_app/app.py",
    '        self.global_search_file_filter: Optional[int] = None\n        self.global_search_preview = True\n',
    '        self.global_search_file_filter: Optional[int] = None\n        self.global_search_preview = True\n        self.global_search_expanded_pane: Optional[int] = None\n        self.global_search_hit_regions: List[Tuple[int, int, int, int, str, int]] = []\n',
)

old_cycle = '''    def _cycle_global_search_file_filter(self, backwards: bool = False) -> None:
        choices = [None] + list(range(len(self.panes)))
        try:
            index = choices.index(self.global_search_file_filter)
        except ValueError:
            index = 0
        delta = -1 if backwards else 1
        self.global_search_file_filter = choices[(index + delta) % len(choices)] if choices else None
        self.global_search_selected = 0
        self._refresh_global_search_results()

'''
new_cycle = '''    def _expand_selected_global_search_file(self) -> None:
        if self.global_search_sort != SORT_FILE or not self.global_search_results:
            if self.global_search_sort != SORT_FILE:
                self.global_search_expanded_pane = None
            return
        selected = min(max(0, self.global_search_selected), len(self.global_search_results) - 1)
        self.global_search_expanded_pane = self.global_search_results[selected].pane_index

    def _jump_global_search_file(self, backwards: bool) -> None:
        self._refresh_global_search_results()
        if not self.global_search_results:
            return
        selected = min(max(0, self.global_search_selected), len(self.global_search_results) - 1)
        current_pane = self.global_search_results[selected].pane_index
        scan = range(selected - 1, -1, -1) if backwards else range(selected + 1, len(self.global_search_results))
        target_pane = next(
            (self.global_search_results[index].pane_index for index in scan if self.global_search_results[index].pane_index != current_pane),
            None,
        )
        if target_pane is None:
            return
        self.global_search_selected = next(
            index for index, result in enumerate(self.global_search_results) if result.pane_index == target_pane
        )
        if self.global_search_sort == SORT_FILE:
            self.global_search_expanded_pane = target_pane

    def _cycle_global_search_file_filter(self, backwards: bool = False) -> None:
        choices = [None] + list(range(len(self.panes)))
        try:
            index = choices.index(self.global_search_file_filter)
        except ValueError:
            index = 0
        delta = -1 if backwards else 1
        self.global_search_file_filter = choices[(index + delta) % len(choices)] if choices else None
        self.global_search_selected = 0
        self._refresh_global_search_results()
        self._expand_selected_global_search_file()

    def _handle_global_search_mouse(self, event: MouseEvent) -> None:
        if event.button == "left" and not event.pressed:
            return
        if event.button in ("wheel_up", "wheel_down"):
            self._refresh_global_search_results()
            if self.global_search_results:
                delta = -3 if event.button == "wheel_up" else 3
                self.global_search_selected = min(
                    max(0, self.global_search_selected + delta), len(self.global_search_results) - 1
                )
                self._expand_selected_global_search_file()
                self.dirty = True
            return
        if event.button != "left":
            return
        for x1, y1, x2, y2, kind, value in self.global_search_hit_regions:
            if not (x1 <= event.x < x2 and y1 <= event.y < y2):
                continue
            self._refresh_global_search_results()
            if kind == "file":
                if self.global_search_expanded_pane == value:
                    self.global_search_expanded_pane = None
                else:
                    self.global_search_expanded_pane = value
                    match_index = next(
                        (i for i, result in enumerate(self.global_search_results) if result.pane_index == value),
                        None,
                    )
                    if match_index is not None:
                        self.global_search_selected = match_index
            elif kind == "result" and 0 <= value < len(self.global_search_results):
                self.global_search_selected = value
                if self.global_search_sort == SORT_FILE:
                    self.global_search_expanded_pane = self.global_search_results[value].pane_index
            self.dirty = True
            return

'''
replace_once("src/htail_app/app.py", old_cycle, new_cycle)

replace_once(
    "src/htail_app/app.py",
    '    def _global_search_lines(self, width: int, height: int) -> List[str]:\n        self._refresh_global_search_results()\n',
    '    def _global_search_lines(self, width: int, height: int) -> List[str]:\n        self._refresh_global_search_results()\n        self.global_search_hit_regions.clear()\n',
)
replace_once(
    "src/htail_app/app.py",
    '                preview_enabled=self.global_search_preview,\n                color=self.color,\n',
    '                preview_enabled=self.global_search_preview,\n                color=self.color,\n                expanded_pane=self.global_search_expanded_pane,\n                hit_regions=self.global_search_hit_regions,\n',
)

# Global-search mouse events must be handled before the keyboard-only branch.
replace_once(
    "src/htail_app/app.py",
    '        if self.global_search_active and not isinstance(event, MouseEvent):\n',
    '        if self.global_search_active and isinstance(event, MouseEvent):\n            self._handle_global_search_mouse(event)\n            return False\n\n        if self.global_search_active and not isinstance(event, MouseEvent):\n',
)

replace_once(
    "src/htail_app/app.py",
    '                self._refresh_global_search_results()\n                self.dirty = True\n                return False\n            if key == "CTRL_T":\n',
    '                self._refresh_global_search_results()\n                self._expand_selected_global_search_file()\n                self.dirty = True\n                return False\n            if key == "CTRL_T":\n',
)
replace_once(
    "src/htail_app/app.py",
    '                    self.global_search_selected = 0\n                    self._refresh_global_search_results()\n                self.dirty = True\n                return False\n            if key == "CTRL_F":\n',
    '                    self.global_search_selected = 0\n                    self._refresh_global_search_results()\n                    self._expand_selected_global_search_file()\n                self.dirty = True\n                return False\n            if key == "CTRL_F":\n',
)

# Shift+arrows jump whole file groups.
replace_once(
    "src/htail_app/app.py",
    '            if key in ("UP", "DOWN", "PAGEUP", "PAGEDOWN"):\n                self._refresh_global_search_results()\n',
    '            if key in ("SHIFT_UP", "SHIFT_DOWN"):\n                self._jump_global_search_file(key == "SHIFT_UP")\n                self.dirty = True\n                return False\n            if key in ("UP", "DOWN", "PAGEUP", "PAGEDOWN"):\n                self._refresh_global_search_results()\n',
)
replace_once(
    "src/htail_app/app.py",
    '                    self.global_search_selected = min(\n                        max(0, self.global_search_selected + delta),\n                        len(self.global_search_results) - 1,\n                    )\n                self.dirty = True\n',
    '                    self.global_search_selected = min(\n                        max(0, self.global_search_selected + delta),\n                        len(self.global_search_results) - 1,\n                    )\n                    self._expand_selected_global_search_file()\n                self.dirty = True\n',
)

# Query edits reset selection and expand the first matching file in grouped view.
replace_once(
    "src/htail_app/app.py",
    '                self._refresh_global_search_results()\n                self.dirty = True\n                return False\n            if isinstance(key, str) and len(key) == 1 and key.isprintable():\n                self.global_search_buffer += key\n                self.global_search_selected = 0\n                self._refresh_global_search_results()\n                self.dirty = True\n',
    '                self._refresh_global_search_results()\n                self._expand_selected_global_search_file()\n                self.dirty = True\n                return False\n            if isinstance(key, str) and len(key) == 1 and key.isprintable():\n                self.global_search_buffer += key\n                self.global_search_selected = 0\n                self._refresh_global_search_results()\n                self._expand_selected_global_search_file()\n                self.dirty = True\n',
)

replace_once(
    "src/htail_app/app.py",
    '            self._refresh_global_search_results()\n            self.dirty = True\n            return False\n        if key == "h":\n',
    '            self._refresh_global_search_results()\n            self._expand_selected_global_search_file()\n            self.dirty = True\n            return False\n        if key == "h":\n',
)

# Help/status wording: make letter O unmistakable and expose file-jump/mouse behavior.
replace_once(
    "src/htail_app/app.py",
    '            "                     Ctrl+T case · Ctrl+O sort · Ctrl+F file · Ctrl+P preview",\n',
    '            "                     ↑↓ match · Shift+↑↓ file · Ctrl+T case · Ctrl+O (letter O) sort",\n            "                     Ctrl+F filter · Ctrl+P preview · click file header to expand/collapse",\n',
)
replace_once(
    "src/htail_app/app.py",
    'f"GLOBAL SEARCH · {self._search_mode_name(self.global_search_mode)} · ↑↓ select · Enter jump · Tab mode · Ctrl+T case · Ctrl+O sort · Ctrl+F file',
    'f"GLOBAL SEARCH · {self._search_mode_name(self.global_search_mode)} · ↑↓ match · Shift+↑↓ file · Enter jump · Tab mode · Ctrl+T case · Ctrl+O(letter) sort · Ctrl+F file',
)

# ---------------------------------------------------------------------------
# Version/release notes.
# ---------------------------------------------------------------------------
replace_once("src/htail_app/__init__.py", 'VERSION = "0.16.3"', 'VERSION = "0.16.4"')
notes = Path("RELEASE_NOTES.md")
old_notes = notes.read_text(encoding="utf-8")
notes.write_text(
    """# htail 0.16.4\n\n## Global search UX\n\n"
    "- Fuzzy relevance results now have explicit `#`, `FILE`, `LINE`, `MATCH`, and `SCORE` column headers, with scores aligned in a fixed right-hand column.\n"
    "- Grouped results now have real file expansion state. Click a file header to expand/collapse it; clicking a result selects it.\n"
    "- Shift+Up / Shift+Down jumps directly between files with matches, while Up / Down continues to move match-by-match.\n"
    "- Global-search mouse wheel navigation is supported inside the results list.\n"
    "- The sort shortcut is now displayed as `Ctrl+O(letter)` / `Ctrl+O (letter O)` so it cannot be confused with terminal `Ctrl+0` zoom/reset shortcuts.\n\n"
    "## Regression coverage\n\n"
    "- Added renderer, shifted-key decoding, file-jump, expansion/collapse, and mouse hit-target tests.\n\n"
    + old_notes,
    encoding="utf-8",
)

# ---------------------------------------------------------------------------
# Focused 0.16.4 regression tests.
# ---------------------------------------------------------------------------
Path("tests/test_global_search_0164.py").write_text(r'''from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from htail_app import app, core
from htail_app.app import MultiApp
from htail_app.global_search import SORT_FILE, SORT_RELEVANCE, render_global_search
from htail_app.input import MouseEvent, parse_escape_sequence
from htail_app.searching import GlobalSearchMatch, SEARCH_FUZZY, SEARCH_SIMPLE


class GlobalSearch0164RendererTests(unittest.TestCase):
    def test_fuzzy_relevance_has_explicit_column_headers_and_sort_hint(self):
        result = GlobalSearchMatch(0, 10, "coordination.md", "2026 verification record", 0, 4, 97.0)
        rows = render_global_search(
            140, 32,
            query="2026",
            mode=SEARCH_FUZZY,
            mode_labels=((SEARCH_SIMPLE, "Simple"), (SEARCH_FUZZY, "Fuzzy")),
            ignore_case=False,
            sort_mode=SORT_RELEVANCE,
            file_filter_label="[All files]",
            results=[result],
            selected=0,
            truncated=False,
            error=None,
            panes=[],
            preview_enabled=False,
            color=False,
        )
        screen = "\n".join(rows)
        self.assertIn("FILE", screen)
        self.assertIn("LINE", screen)
        self.assertIn("MATCH", screen)
        self.assertIn("SCORE", screen)
        self.assertIn("Ctrl+O(letter) sort", screen)

    def test_group_headers_reflect_explicit_expansion_state(self):
        results = [
            GlobalSearchMatch(0, 0, "a.txt", "2026 a", 0, 4, None),
            GlobalSearchMatch(1, 0, "b.txt", "2026 b", 0, 4, None),
        ]
        rows = render_global_search(
            120, 28,
            query="2026",
            mode=SEARCH_SIMPLE,
            mode_labels=((SEARCH_SIMPLE, "Simple"),),
            ignore_case=False,
            sort_mode=SORT_FILE,
            file_filter_label="[All files]",
            results=results,
            selected=0,
            truncated=False,
            error=None,
            panes=[],
            preview_enabled=False,
            color=False,
            expanded_pane=1,
        )
        screen = "\n".join(rows)
        self.assertIn("▶ a.txt", screen)
        self.assertIn("▼ b.txt", screen)


class GlobalSearch0164InputTests(unittest.TestCase):
    def test_shift_arrows_decode(self):
        self.assertEqual(parse_escape_sequence("\x1b[1;2A"), "SHIFT_UP")
        self.assertEqual(parse_escape_sequence("\x1b[1;2B"), "SHIFT_DOWN")


class GlobalSearch0164InteractionTests(unittest.TestCase):
    def make_app(self, root: Path) -> MultiApp:
        a = root / "a.txt"
        b = root / "b.txt"
        c = root / "c.txt"
        a.write_text("2026 a-one\n2026 a-two\n", encoding="utf-8")
        b.write_text("2026 b-one\n2026 b-two\n", encoding="utf-8")
        c.write_text("2026 c-one\n", encoding="utf-8")
        args = app.parse_args([str(a), str(b), str(c), "--no-native-watch", "--no-color"])
        application = MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))
        application.handle_input("g")
        for ch in "2026":
            application.handle_input(ch)
        return application

    def test_shift_up_down_jumps_file_groups(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                self.assertEqual(application.global_search_results[application.global_search_selected].pane_index, 0)
                self.assertEqual(application.global_search_expanded_pane, 0)
                application.handle_input("SHIFT_DOWN")
                self.assertEqual(application.global_search_results[application.global_search_selected].pane_index, 1)
                self.assertEqual(application.global_search_expanded_pane, 1)
                application.handle_input("SHIFT_UP")
                self.assertEqual(application.global_search_results[application.global_search_selected].pane_index, 0)
                self.assertEqual(application.global_search_expanded_pane, 0)
            finally:
                application.close_native_watch()

    def test_mouse_header_expands_and_collapses_file(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application._global_search_lines(140, 32)
                header = next(region for region in application.global_search_hit_regions if region[4:] == ("file", 1))
                x1, y1, _, _, _, _ = header
                application.handle_input(MouseEvent(x1 + 1, y1, "left", True))
                self.assertEqual(application.global_search_expanded_pane, 1)
                self.assertEqual(application.global_search_results[application.global_search_selected].pane_index, 1)

                application._global_search_lines(140, 32)
                header = next(region for region in application.global_search_hit_regions if region[4:] == ("file", 1))
                x1, y1, _, _, _, _ = header
                application.handle_input(MouseEvent(x1 + 1, y1, "left", True))
                self.assertIsNone(application.global_search_expanded_pane)
            finally:
                application.close_native_watch()

    def test_mouse_result_click_selects_visible_match(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application._global_search_lines(140, 32)
                result_region = next(
                    region for region in application.global_search_hit_regions
                    if region[4] == "result" and region[5] == 1
                )
                x1, y1, _, _, _, _ = result_region
                application.handle_input(MouseEvent(x1 + 1, y1, "left", True))
                self.assertEqual(application.global_search_selected, 1)
            finally:
                application.close_native_watch()


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

print("staged htail 0.16.4 global search UX patch")
