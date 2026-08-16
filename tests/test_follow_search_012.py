from __future__ import annotations

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
        self.assertEqual(pane.search_badge_text(), "1/3 MATCHES")
        selected_rows = pane._snapshot_visual_lines
        self.assertTrue(any("\x1b[1;30;48;5;208m" in row and "foo" in core.strip_ansi(row) for row in selected_rows))
        self.assertTrue(any("\x1b[7m" in row and "foo" in core.strip_ansi(row) for row in selected_rows))

        self.assertTrue(pane.search_next(False, 40, 4))
        pane.render_box(40, 6, True, 0)
        self.assertEqual(pane._search_match_position, 2)
        self.assertEqual(pane.search_badge_text(), "2/3 MATCHES")

        self.assertTrue(pane.search_next(False, 40, 4))
        self.assertTrue(pane.search_next(False, 40, 4))
        self.assertEqual(pane._search_match_position, 1)
        self.assertEqual(pane.search_badge_text(), "1/3 MATCHES")

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
