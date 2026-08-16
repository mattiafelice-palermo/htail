from __future__ import annotations

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

    def test_selected_match_is_guaranteed_black_on_orange(self):
        pane = self.make_pane()
        pane.render_box(60, 7, True, 0)
        selected = pane._snapshot_visual_lines[pane._snapshot_source_to_visual[pane._search_last_target]]
        self.assertIn("\x1b[1;30;48;5;208m", selected)
        self.assertIn("foo", core.strip_ansi(selected))

    def test_match_progress_is_no_longer_embedded_in_top_border(self):
        pane = self.make_pane()
        top = core.strip_ansi(pane.render_box(80, 7, True, 0)[0])
        self.assertNotIn("MATCH", top)
        self.assertEqual(pane.search_badge_text(), "1/3 MATCHES")


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
