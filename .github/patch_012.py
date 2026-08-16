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


def replace_method(text, name, replacement):
    pattern = rf"(?ms)^    def {re.escape(name)}\b.*?(?=^    def |\Z)"
    new, count = re.subn(pattern, lambda _m: replacement.rstrip() + "\n\n", text, count=1)
    if count != 1:
        raise RuntimeError(f"method {name}: expected one match, found {count}")
    return new


# ---------------------------------------------------------------------------
# Pane behavior: robust startup EOF, CHANGES/TAIL modes, selected search match.
# ---------------------------------------------------------------------------
pane_path = "src/htail_app/pane.py"
pane = read(pane_path)
pane = replace_once(
    pane,
    "from .searching import SEARCH_REGEX, SEARCH_SIMPLE, compile_search, search_label\n",
    "from .searching import SEARCH_REGEX, SEARCH_SIMPLE, compile_search, search_label\n\n\nFOLLOW_CHANGES = \"changes\"\nFOLLOW_TAIL = \"tail\"\n",
    "follow constants",
)
pane = replace_once(
    pane,
    "        self._initial_bottom_pending = False\n",
    "        self._initial_bottom_pending = False\n        # Startup follows EOF until the user actually navigates or the first\n        # update arrives. Unlike the legacy one-shot flag, this survives a\n        # terminal/layout geometry change between the first two renders.\n        self._startup_follow_eof = True\n        self.follow_mode = FOLLOW_CHANGES\n        self.tail_auto_follow = True\n        self._snapshot_tail_pending = False\n",
    "startup/follow state",
)
pane = replace_once(
    pane,
    "        self._search_last_target: Optional[int] = None\n",
    "        self._search_last_target: Optional[int] = None\n        self._search_match_position: Optional[int] = None\n        self._search_match_total = 0\n",
    "search count state",
)

pane = replace_method(pane, "_apply_regex_marks", r'''    def _apply_regex_marks(self, row: str, search_index: Optional[int] = None) -> str:
        if not self.color:
            return row
        # Underline is the persistent user highlight. Non-selected search
        # matches keep reverse video; the currently selected n/N match gets a
        # bright-yellow background so it is immediately distinguishable while
        # preserving the row's existing foreground/syntax colour.
        row = _inject_regex_style(row, self.highlight_regex, "\x1b[4m", "\x1b[24m")
        if self.search_regex is not None:
            if search_index is not None and search_index == self._search_last_target:
                row = _inject_regex_style(row, self.search_regex, "\x1b[103m", "\x1b[49m")
            else:
                row = _inject_regex_style(row, self.search_regex, "\x1b[7m", "\x1b[27m")
        return row''')

pane = replace_method(pane, "set_search", r'''    def set_search(self, expression: str, flags: int = 0, mode: str = SEARCH_REGEX) -> Optional[str]:
        if not expression:
            self.search_pattern = ""
            self.search_mode = mode
            self.search_regex = None
            self._search_last_target = None
            self._search_match_position = None
            self._search_match_total = 0
            self._mark_layout_dirty()
            self._snapshot_layout_dirty = True
            return None
        compiled, error = compile_search(expression, mode, flags)
        if error is not None:
            return error
        self.search_pattern = expression
        self.search_mode = mode
        self.search_regex = compiled
        self._search_last_target = None
        self._refresh_search_position()
        self._mark_layout_dirty()
        self._snapshot_layout_dirty = True
        return None''')

pane = replace_method(pane, "_search_display", r'''    def _search_display(self) -> str:
        return search_label(self.search_pattern, self.search_mode)

    def _search_candidates(self) -> List[int]:
        pattern = self.search_regex
        if pattern is None:
            return []
        if self.snapshot_raw:
            return [
                i for i, line in enumerate(self.snapshot_raw)
                if self.display_filter.accepts(line)
                and pattern.search(line.rstrip("\r\n")) is not None
            ]
        return [
            i for i, line in enumerate(self.lines)
            if pattern.search(core.strip_ansi(line)) is not None
        ]

    def _refresh_search_position(self) -> List[int]:
        candidates = self._search_candidates()
        self._search_match_total = len(candidates)
        if self._search_last_target in candidates:
            self._search_match_position = candidates.index(self._search_last_target) + 1
        else:
            self._search_match_position = None
        return candidates

    def _set_search_target(self, target: int) -> None:
        self._search_last_target = target
        self._refresh_search_position()
        # Search styling is cached in both layouts; selecting another hit must
        # repaint the old and new selected lines.
        self._mark_layout_dirty()
        self._snapshot_layout_dirty = True''')

pane = replace_method(pane, "jump_to_source_line", r'''    def jump_to_source_line(self, source_index: int, width: int, body_height: int) -> bool:
        """Show a current-snapshot source line, centered when geometry allows."""
        if not self.snapshot_raw:
            return False
        self._startup_follow_eof = False
        if self.follow_mode == FOLLOW_TAIL:
            self.tail_auto_follow = False
        self.prefer_snapshot = True
        self._snapshot_anchor_pending = False
        self._snapshot_tail_pending = False
        self._ensure_snapshot_layout(max(1, width))
        visual = self._snapshot_source_to_visual.get(source_index)
        if visual is None:
            return False
        body_height = max(1, body_height)
        desired = max(0, visual - body_height // 2)
        self._snapshot_top = min(desired, self._snapshot_max_top(body_height))
        self._set_search_target(source_index)
        return True''')

pane = replace_method(pane, "search_next", r'''    def search_next(self, reverse: bool, width: int, body_height: int) -> bool:
        pattern = self.search_regex
        if pattern is None:
            self.set_message("no active search")
            return False

        self._startup_follow_eof = False
        if self.follow_mode == FOLLOW_TAIL:
            self.tail_auto_follow = False
        width = max(1, width)
        body_height = max(1, body_height)
        candidates = self._refresh_search_position()
        if not candidates:
            self._search_last_target = None
            self._search_match_position = None
            self._mark_layout_dirty()
            self._snapshot_layout_dirty = True
            self.set_message(f"no match: {self._search_display()}")
            return False

        if self.snapshot_raw:
            self.prefer_snapshot = True
            self._snapshot_anchor_pending = False
            self._snapshot_tail_pending = False
            self._ensure_snapshot_layout(width)
            current_source = self._search_last_target if self._search_last_target is not None else -1
            if self._search_last_target is None and self._snapshot_visual_to_source:
                start = min(max(0, self._snapshot_top), len(self._snapshot_visual_to_source) - 1)
                for source in self._snapshot_visual_to_source[start:]:
                    if source is not None:
                        current_source = source
                        break
            if reverse:
                prior = [i for i in candidates if i < current_source]
                target = prior[-1] if prior else candidates[-1]
            else:
                later = [i for i in candidates if i > current_source]
                target = later[0] if later else candidates[0]
            self._snapshot_top = min(
                self._snapshot_source_to_visual[target],
                self._snapshot_max_top(body_height),
            )
            self._set_search_target(target)
            self.set_message(f"match {self._search_match_position}/{self._search_match_total}: {self._search_display()}")
            return True

        self._ensure_layout(width)
        current = self._search_last_target if self._search_last_target is not None else self._logical_at_top()
        if reverse:
            prior = [i for i in candidates if i < current]
            target = prior[-1] if prior else candidates[-1]
        else:
            later = [i for i in candidates if i > current]
            target = later[0] if later else candidates[0]
        self.top = min(self._logical_to_visual[target], self._max_top(body_height))
        self._set_search_target(target)
        self.set_message(f"match {self._search_match_position}/{self._search_match_total}: {self._search_display()}")
        return True''')

pane = replace_method(pane, "set_snapshot", r'''    def set_snapshot(
        self,
        raw_lines: Sequence[str],
        changed_indices: Sequence[int] = (),
        *,
        prefer: bool = False,
        update_header: Optional[str] = None,
    ) -> None:
        self.snapshot_raw = list(raw_lines)
        self.snapshot_changed = set(changed_indices)
        self.snapshot_update_header = update_header
        self._snapshot_layout_dirty = True
        if self.search_regex is not None:
            self._refresh_search_position()
        if prefer:
            self._startup_follow_eof = False
            if self.follow_mode == FOLLOW_TAIL:
                if self.tail_auto_follow:
                    self.prefer_snapshot = True
                    self._snapshot_tail_pending = True
                    self._snapshot_anchor_pending = False
                # If the user manually left EOF in TAIL mode, retain whichever
                # view they are inspecting instead of yanking them back.
            else:
                self.prefer_snapshot = True
                self._snapshot_anchor_pending = True
                self._snapshot_tail_pending = False''')

pane = replace_once(
    pane,
    "            wrapped = self._wrap_cached(self._apply_regex_marks(line), width)\n",
    "            wrapped = self._wrap_cached(self._apply_regex_marks(line, logical_index), width)\n",
    "history selected search styling",
)
pane = replace_once(
    pane,
    "            row = self._apply_regex_marks(row)\n",
    "            row = self._apply_regex_marks(row, source_index)\n",
    "snapshot selected search styling",
)
pane = replace_once(
    pane,
    "        self._snapshot_visual_lines = visual\n        if self._snapshot_anchor_pending:\n            self._snapshot_top = anchor if anchor is not None else max(0, len(visual) - 1)\n            self._snapshot_anchor_pending = False\n        else:\n            self._snapshot_top = min(max(0, self._snapshot_top), max(0, len(visual) - 1))\n",
    "        self._snapshot_visual_lines = visual\n        if self._snapshot_tail_pending:\n            # Body height is not known here; park at the final row and the\n            # viewport clamp in render_box/_snapshot_view_rows will convert it\n            # to the last full screenful.\n            self._snapshot_top = max(0, len(visual) - 1)\n            self._snapshot_tail_pending = False\n        elif self._snapshot_anchor_pending:\n            self._snapshot_top = anchor if anchor is not None else max(0, len(visual) - 1)\n            self._snapshot_anchor_pending = False\n        else:\n            self._snapshot_top = min(max(0, self._snapshot_top), max(0, len(visual) - 1))\n",
    "snapshot follow positioning",
)

pane = replace_method(pane, "add_initial", r'''    def add_initial(self, raw_lines: Sequence[str]) -> None:
        visible = [line for line in raw_lines if self.display_filter.accepts(line)]
        self.lines.extend(core.render_initial_lines(visible, self.highlighter))
        self._mark_layout_dirty()
        self._initial_bottom_pending = True
        self._startup_follow_eof = True
        self.waiting = False
        self.missing = False''')

pane = replace_method(pane, "_apply_initial_bottom", r'''    def _apply_initial_bottom(self, body_height: int) -> None:
        # Keep following EOF across startup geometry/layout changes until the
        # user explicitly navigates or a real update establishes follow-mode
        # semantics. This fixes the old one-shot flag being consumed too early.
        if self._startup_follow_eof or self._initial_bottom_pending:
            self.top = self._max_top(body_height)
            self._initial_bottom_pending = False''')

pane = replace_method(pane, "toggle_pause", r'''    def toggle_follow_mode(self) -> None:
        self._startup_follow_eof = False
        if self.follow_mode == FOLLOW_CHANGES:
            self.follow_mode = FOLLOW_TAIL
            self.tail_auto_follow = True
            if self.snapshot_raw:
                self.prefer_snapshot = True
                self._snapshot_anchor_pending = False
                self._snapshot_tail_pending = True
                self._snapshot_layout_dirty = True
            else:
                self._initial_bottom_pending = True
            self.set_message("follow mode: TAIL")
        else:
            self.follow_mode = FOLLOW_CHANGES
            self._snapshot_tail_pending = False
            self.set_message("follow mode: CHANGES")

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            self.set_message("paused")
        else:
            self.unseen_updates = 0
            if self.updates and self.snapshot_raw:
                self.prefer_snapshot = True
                if self.follow_mode == FOLLOW_TAIL:
                    self.tail_auto_follow = True
                    self._snapshot_anchor_pending = False
                    self._snapshot_tail_pending = True
                    self._snapshot_layout_dirty = True
                else:
                    self._snapshot_tail_pending = False
                    self._snapshot_anchor_pending = True
            self.set_message("resumed at freshest update")''')

pane = replace_method(pane, "freshest", r'''    def freshest(self) -> None:
        self._startup_follow_eof = False
        if self.snapshot_raw:
            self.unseen_updates = 0
            self.prefer_snapshot = True
            if self.follow_mode == FOLLOW_TAIL:
                self.tail_auto_follow = True
                self._snapshot_anchor_pending = False
                self._snapshot_tail_pending = True
                self._snapshot_layout_dirty = True
            elif self.updates:
                self._snapshot_tail_pending = False
                self._snapshot_anchor_pending = True''')

pane = replace_method(pane, "previous_update", r'''    def previous_update(self) -> None:
        if not self.updates:
            return
        self._startup_follow_eof = False
        if self.follow_mode == FOLLOW_TAIL:
            self.tail_auto_follow = False
        self.prefer_snapshot = False
        current = self._logical_at_top()
        candidates = [u for u in self.updates if u.start < current]
        target = candidates[-1] if candidates else self.updates[0]
        self._pending_anchor_logical = target.start''')

pane = replace_method(pane, "next_update", r'''    def next_update(self) -> None:
        if not self.updates:
            return
        self._startup_follow_eof = False
        if self.follow_mode == FOLLOW_TAIL:
            self.tail_auto_follow = False
        self.prefer_snapshot = False
        current = self._logical_at_top()
        candidates = [u for u in self.updates if u.start > current]
        target = candidates[0] if candidates else self.updates[-1]
        self._pending_anchor_logical = target.start''')

pane = replace_method(pane, "scroll", r'''    def scroll(self, command: str, body_height: int) -> None:
        page = max(1, body_height - 2)
        self._startup_follow_eof = False

        # END is an explicit "return to live tail" action in TAIL mode even if
        # the user is currently looking at historical update records.
        if self.follow_mode == FOLLOW_TAIL and command == "END" and self.snapshot_raw:
            self.tail_auto_follow = True
            self.prefer_snapshot = True
            self._snapshot_anchor_pending = False
            self._snapshot_tail_pending = True
            self._snapshot_layout_dirty = True
            return

        if self.follow_mode == FOLLOW_TAIL and command in ("UP", "PAGEUP", "HOME"):
            self.tail_auto_follow = False

        if self.prefer_snapshot and self.snapshot_raw:
            if not self._snapshot_visual_lines:
                return
            if command == "UP":
                self._snapshot_top -= 1
            elif command == "DOWN":
                self._snapshot_top += 1
            elif command == "PAGEUP":
                self._snapshot_top -= page
            elif command == "PAGEDOWN":
                self._snapshot_top += page
            elif command == "HOME":
                self._snapshot_top = 0
            elif command == "END":
                self._snapshot_top = self._snapshot_max_top(body_height)
            self._snapshot_top = min(max(0, self._snapshot_top), self._snapshot_max_top(body_height))
            if self.follow_mode == FOLLOW_TAIL and command in ("DOWN", "PAGEDOWN"):
                if self._snapshot_top >= self._snapshot_max_top(body_height):
                    self.tail_auto_follow = True
            return

        if not self._visual_lines:
            return
        if command == "UP":
            self.top -= 1
        elif command == "DOWN":
            self.top += 1
        elif command == "PAGEUP":
            self.top -= page
        elif command == "PAGEDOWN":
            self.top += page
        elif command == "HOME":
            self.top = 0
        elif command == "END":
            self.top = self._max_top(body_height)
        self.top = min(max(0, self.top), self._max_top(body_height))''')

pane = replace_once(
    pane,
    "        parts = [f\"{index + 1}:{self.name}\", state]\n",
    "        parts = [f\"{index + 1}:{self.name}\", state, self.follow_mode.upper()]\n        if self.search_regex is not None:\n            position = self._search_match_position or 0\n            parts.append(f\"MATCH {position}/{self._search_match_total}\")\n",
    "title follow/search status",
)
write(pane_path, pane)


# ---------------------------------------------------------------------------
# App controls/help/footer for the per-pane follow-mode toggle.
# ---------------------------------------------------------------------------
app_path = "src/htail_app/app.py"
app = read(app_path)
app = replace_once(
    app,
    '            "  p                  pause/resume automatic jumps",\n',
    '            "  p                  pause/resume automatic jumps",\n            "  t                  toggle CHANGES / TAIL follow mode",\n',
    "help follow mode",
)
app = replace_once(
    app,
    '        controls = "/ search · g global · n/N match · h highlight · Tab pane · l layout · ↑↓/Pg scroll · [/] update · f newest · p pause · u update · q quit · ? help"\n',
    '        controls = "/ search · g global · n/N match · h highlight · Tab pane · l layout · ↑↓/Pg scroll · [/] update · f newest · t follow · p pause · u update · q quit · ? help"\n',
    "footer follow mode",
)
app = replace_once(
    app,
    '        if key in ("p", "P"):\n            pane.toggle_pause(); self.dirty = True; return False\n',
    '        if key in ("p", "P"):\n            pane.toggle_pause(); self.dirty = True; return False\n        if key in ("t", "T"):\n            pane.toggle_follow_mode(); self.dirty = True; return False\n',
    "follow toggle key",
)
write(app_path, app)


# ---------------------------------------------------------------------------
# Version/docs/release notes.
# ---------------------------------------------------------------------------
init_path = "src/htail_app/__init__.py"
init = read(init_path)
init = replace_once(init, 'VERSION = "0.11.0"', 'VERSION = "0.12.0"', "version")
write(init_path, init)

readme_path = "README.md"
readme = read(readme_path)
readme = replace_once(
    readme,
    '| `p` | Pause/resume automatic jumps in focused pane |\n',
    '| `p` | Pause/resume automatic jumps in focused pane |\n| `t` | Toggle focused pane between **CHANGES** and **TAIL** follow modes |\n',
    "README control",
)
readme = replace_once(
    readme,
    'Search and highlight prompts use regular expressions. `-I` / `--ignore-case` also applies to interactive regexes. Search matches are shown with reverse video; persistent highlights use underline so existing syntax colors remain visible.\n',
    'Simple search is the default; `Tab` switches its prompt to explicit regex mode. `-I` / `--ignore-case` applies to both. Search matches use reverse video, while the currently selected `n` / `N` match gets a bright-yellow background and the pane title shows `MATCH x/y`. Persistent regex highlights use underline so existing syntax colors remain visible.\n',
    "README search style",
)
readme = replace_once(
    readme,
    'A new update moves **only its own pane** to the beginning of that update. Other panes keep their current reading position. While a pane is paused, changes are still captured and its title reports unseen updates. The focused pane is visually distinguished from the others.\n',
    'Every pane starts at **EOF** on first open. In the default **CHANGES** follow mode, a new update moves only its own pane to the first changed/new line. Press `t` to switch that pane to **TAIL** mode, where updates keep the viewport at EOF like `tail -f`. Manually scrolling upward in TAIL mode suspends auto-follow so the viewport is not yanked away; `f`, `End`, or scrolling back to EOF resumes it. Other panes keep their current reading position. While a pane is paused, changes are still captured and its title reports unseen updates.\n',
    "README follow semantics",
)
write(readme_path, readme)

notes_path = "RELEASE_NOTES.md"
notes = read(notes_path)
new_notes = '''# htail 0.12.0

## New features

- Added per-pane **CHANGES / TAIL** follow modes. CHANGES remains the default and opens new updates at their first changed/new line; TAIL stays pinned to EOF for continuously growing program output.
- Press `t` to toggle the focused pane's follow mode. Manual upward navigation suspends TAIL auto-follow; `f`, `End`, or returning to EOF resumes it.
- Local Simple and Regex searches now show the selected `n` / `N` match with a distinct bright-yellow background, while other matches retain reverse-video highlighting.
- Pane titles now show persistent `MATCH x/y` search position and the active follow mode.

## Bug fixes

- Initial file viewing now remains bottom-aligned across startup terminal/layout geometry changes instead of consuming the EOF-position request after the first render and sometimes falling back to the top with `↓N more`.
- Search selection styling is repainted when moving between matches, so the active result never remains visually ambiguous.
'''
write(notes_path, new_notes)


# ---------------------------------------------------------------------------
# Differential reference: normalize the intentional default CHANGES title tag
# so the v0.9.0 gate still compares invariant content/render behavior.
# ---------------------------------------------------------------------------
probe_path = "benchmarks/reference_probe.py"
probe = read(probe_path)
probe = replace_once(
    probe,
    'def _plain(core, rows):\n    return [core.strip_ansi(row) for row in rows]\n',
    'def _normalize_intentional_ui(text: str) -> str:\n    # 0.12.0 intentionally exposes the default follow mode in pane titles.\n    # Strip only that new label when comparing invariant behavior to v0.9.0.\n    return text.replace(" · CHANGES", "")\n\n\ndef _plain(core, rows):\n    return [_normalize_intentional_ui(core.strip_ansi(row)) for row in rows]\n',
    "reference title normalization",
)
probe = replace_once(
    probe,
    '        behavior["final_terminal_body"] = emulate_terminal(combined_output, 120, 40)[:-2]\n',
    '        behavior["final_terminal_body"] = [_normalize_intentional_ui(row) for row in emulate_terminal(combined_output, 120, 40)[:-2]]\n',
    "reference terminal normalization",
)
write(probe_path, probe)


# ---------------------------------------------------------------------------
# Regression coverage.
# ---------------------------------------------------------------------------
tests = r'''from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from htail_app import app, core
from htail_app.app import MultiApp
from htail_app.pane import FOLLOW_CHANGES, FOLLOW_TAIL, Pane
from htail_app.searching import SEARCH_REGEX, SEARCH_SIMPLE


class FollowModeTests(unittest.TestCase):
    def make_pane(self, *, color=False):
        path = Path("follow.txt")
        pane = Pane(path, core.SyntaxHighlighter(path, "none", color), core.DisplayFilter(), color, 0.0)
        initial = [f"line {i}\n" for i in range(30)]
        pane.add_initial(initial)
        pane.set_snapshot(initial)
        return pane, initial

    def test_startup_eof_survives_geometry_change_before_user_navigation(self):
        pane, _ = self.make_pane()
        # First render is tall enough to consume the old one-shot bottom flag
        # at top=0. A later shorter geometry used to remain at the top.
        pane.render_box(40, 40, True, 0)
        rows = [core.strip_ansi(row) for row in pane.render_box(40, 8, True, 0)]
        self.assertTrue(any("line 29" in row for row in rows))
        self.assertFalse(any("line 0 " in row for row in rows))
        self.assertNotIn("↓", core.strip_ansi(pane.title(0, 80, True, 6)))

    def test_changes_mode_update_opens_at_first_changed_line(self):
        pane, initial = self.make_pane()
        pane.render_box(40, 8, True, 0)
        current = list(initial)
        current[8] = "CHANGED HERE\n"
        current.extend(["new 30\n", "new 31\n"])
        pane.set_snapshot(current, [8, 30, 31], prefer=True, update_header="UPDATE-MARKER")
        rows = [core.strip_ansi(row) for row in pane.render_box(40, 8, True, 0)]
        self.assertEqual(pane.follow_mode, FOLLOW_CHANGES)
        self.assertTrue(any("UPDATE-MARKER" in row for row in rows))
        self.assertTrue(any("CHANGED HERE" in row for row in rows))

    def test_tail_mode_update_stays_at_eof(self):
        pane, initial = self.make_pane()
        pane.toggle_follow_mode()
        self.assertEqual(pane.follow_mode, FOLLOW_TAIL)
        current = list(initial) + [f"new {i}\n" for i in range(10)]
        pane.set_snapshot(current, list(range(30, 40)), prefer=True, update_header="UPDATE-MARKER")
        rows = [core.strip_ansi(row) for row in pane.render_box(40, 8, True, 0)]
        self.assertTrue(any("new 9" in row for row in rows))
        self.assertFalse(any("UPDATE-MARKER" in row for row in rows))
        self.assertTrue(pane.tail_auto_follow)

    def test_tail_manual_scroll_suspends_updates_until_freshest(self):
        pane, initial = self.make_pane()
        pane.toggle_follow_mode()
        current = list(initial) + [f"new {i}\n" for i in range(10)]
        pane.set_snapshot(current, list(range(30, 40)), prefer=True, update_header="U1")
        pane.render_box(40, 8, True, 0)
        pane.scroll("UP", 6)
        self.assertFalse(pane.tail_auto_follow)
        old_top = pane._snapshot_top

        newer = current + ["latest A\n", "latest B\n"]
        pane.set_snapshot(newer, [40, 41], prefer=True, update_header="U2")
        pane.render_box(40, 8, True, 0)
        self.assertEqual(pane._snapshot_top, old_top)
        self.assertFalse(pane.tail_auto_follow)

        pane.freshest()
        rows = [core.strip_ansi(row) for row in pane.render_box(40, 8, True, 0)]
        self.assertTrue(pane.tail_auto_follow)
        self.assertTrue(any("latest B" in row for row in rows))

    def test_end_resumes_tail_even_from_manual_navigation(self):
        pane, initial = self.make_pane()
        pane.toggle_follow_mode()
        current = list(initial) + ["tail end\n"]
        pane.set_snapshot(current, [30], prefer=True, update_header="U")
        pane.render_box(40, 8, True, 0)
        pane.scroll("HOME", 6)
        self.assertFalse(pane.tail_auto_follow)
        pane.scroll("END", 6)
        rows = [core.strip_ansi(row) for row in pane.render_box(40, 8, True, 0)]
        self.assertTrue(pane.tail_auto_follow)
        self.assertTrue(any("tail end" in row for row in rows))

    def test_title_exposes_follow_mode(self):
        pane, _ = self.make_pane()
        self.assertIn("CHANGES", core.strip_ansi(pane.title(0, 100, True, 6)))
        pane.toggle_follow_mode()
        self.assertIn("TAIL", core.strip_ansi(pane.title(0, 100, True, 6)))


class SearchSelectionTests(unittest.TestCase):
    def make_pane(self, mode):
        path = Path("search.txt")
        pane = Pane(path, core.SyntaxHighlighter(path, "none", True), core.DisplayFilter(), True, 0.0)
        rows = ["zero foo\n", "one\n", "two foo\n", "three foo\n"]
        pane.add_initial(rows)
        pane.set_snapshot(rows)
        expression = "foo" if mode == SEARCH_SIMPLE else r"f.o"
        self.assertIsNone(pane.set_search(expression, mode=mode))
        return pane

    def assert_selected_progress(self, mode):
        pane = self.make_pane(mode)
        self.assertTrue(pane.search_next(False, 40, 4))
        pane.render_box(40, 6, True, 0)
        self.assertEqual((pane._search_match_position, pane._search_match_total), (1, 3))
        self.assertIn("MATCH 1/3", core.strip_ansi(pane.title(0, 100, True, 4)))
        selected_rows = pane._snapshot_visual_lines
        self.assertTrue(any("\x1b[103m" in row and "foo" in core.strip_ansi(row) for row in selected_rows))
        self.assertTrue(any("\x1b[7m" in row and "foo" in core.strip_ansi(row) for row in selected_rows))

        self.assertTrue(pane.search_next(False, 40, 4))
        pane.render_box(40, 6, True, 0)
        self.assertEqual(pane._search_match_position, 2)
        self.assertIn("MATCH 2/3", core.strip_ansi(pane.title(0, 100, True, 4)))

        self.assertTrue(pane.search_next(False, 40, 4))
        self.assertTrue(pane.search_next(False, 40, 4))
        self.assertEqual(pane._search_match_position, 1)
        self.assertIn("MATCH 1/3", core.strip_ansi(pane.title(0, 100, True, 4)))

    def test_simple_search_selected_match_and_counter(self):
        self.assert_selected_progress(SEARCH_SIMPLE)

    def test_regex_search_selected_match_and_counter(self):
        self.assert_selected_progress(SEARCH_REGEX)


class FollowModeAppInteractionTests(unittest.TestCase):
    def test_t_toggles_only_focused_pane(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = root / "a.txt"
            b = root / "b.txt"
            a.write_text("a\n", encoding="utf-8")
            b.write_text("b\n", encoding="utf-8")
            args = app.parse_args([str(a), str(b), "--no-native-watch", "--no-color"])
            application = MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))
            try:
                self.assertEqual(application.panes[0].follow_mode, FOLLOW_CHANGES)
                self.assertEqual(application.panes[1].follow_mode, FOLLOW_CHANGES)
                application.handle_input("t")
                self.assertEqual(application.panes[0].follow_mode, FOLLOW_TAIL)
                self.assertEqual(application.panes[1].follow_mode, FOLLOW_CHANGES)
                application.handle_input("TAB")
                application.handle_input("t")
                self.assertEqual(application.panes[1].follow_mode, FOLLOW_TAIL)
            finally:
                application.close_native_watch()


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_follow_search_012.py", tests)

print("htail 0.12.0 patch applied")
