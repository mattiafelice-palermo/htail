from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from htail_app import app, core
from htail_app.app import MultiApp
from htail_app.input import normalize_plain_key


class Search014Tests(unittest.TestCase):
    def make_app(self, root: Path, *, color=False, text="alpha foo\nbeta\ngamma foo\ndelta\n"):
        source = root / "source.txt"
        source.write_text(text, encoding="utf-8")
        args = app.parse_args([str(source), "--no-native-watch"] + (["--no-color"] if not color else []))
        return MultiApp(args, color, core.DisplayFilter(), core.UpdateService(""))

    def type_query(self, application: MultiApp, query: str) -> None:
        application.handle_input("/")
        for ch in query:
            application.handle_input(ch)

    def test_escape_key_normalization_closes_inline_search(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.handle_input("/")
                application.handle_input("f")
                application.handle_input(normalize_plain_key("\x1b"))
                self.assertIsNone(application.prompt_mode)
                self.assertEqual(application.active_pane().search_pattern, "")
            finally:
                application.close_native_watch()

    def test_live_typing_selects_first_match_immediately(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td), color=True)
            try:
                self.type_query(application, "foo")
                pane = application.active_pane()
                self.assertEqual((pane._search_match_position, pane._search_match_total), (1, 2))
                application._frame_rows()
                selected = pane._snapshot_visual_lines[pane._snapshot_source_to_visual[pane._search_last_target]]
                self.assertIn("\x1b[1;30;48;5;208m", selected)
            finally:
                application.close_native_watch()

    def test_up_down_cycle_matches_while_editor_stays_open(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                self.type_query(application, "foo")
                pane = application.active_pane()
                application.handle_input("DOWN")
                self.assertEqual(pane._search_match_position, 2)
                self.assertEqual(application.prompt_mode, "search")
                application.handle_input("UP")
                self.assertEqual(pane._search_match_position, 1)
                self.assertEqual(application.prompt_mode, "search")
            finally:
                application.close_native_watch()

    def test_match_badge_is_inside_panel_and_search_row_is_discoverable(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                self.type_query(application, "foo")
                width, height, _ = application.content_dimensions()
                rows = application._pane_boxes(width, height)
                rect = application.last_rects[0][1]
                local = [core.strip_ansi(row[rect.x:rect.x + rect.width]) for row in rows[rect.y:rect.y + rect.height]]
                self.assertNotIn("MATCH", local[0])
                self.assertTrue(any("1/2 MATCHES" in row for row in local[1:-1]))
                self.assertTrue(any("↑↓ matches" in row and "Ctrl+T" in row for row in local))
            finally:
                application.close_native_watch()

    def test_ctrl_t_toggles_case_and_live_results(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td), text="Foo\nfoo\nFOO\n")
            try:
                self.type_query(application, "foo")
                pane = application.active_pane()
                self.assertEqual(pane._search_match_total, 1)
                self.assertFalse(application.prompt_ignore_case)
                application.handle_input("CTRL_T")
                self.assertTrue(application.prompt_ignore_case)
                self.assertEqual(pane._search_match_total, 3)
                self.assertEqual(pane._search_match_position, 1)
                application.handle_input("CTRL_T")
                self.assertFalse(application.prompt_ignore_case)
                self.assertEqual(pane._search_match_total, 1)
            finally:
                application.close_native_watch()

    def test_committed_case_mode_is_restored_on_reopen_and_escape(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td), text="Foo\nfoo\n")
            try:
                self.type_query(application, "foo")
                application.handle_input("CTRL_T")
                application.handle_input("\r")
                pane = application.active_pane()
                self.assertTrue(bool(pane.search_flags))
                application.handle_input("/")
                self.assertTrue(application.prompt_ignore_case)
                application.handle_input("CTRL_T")
                application.handle_input("ESC")
                self.assertTrue(bool(pane.search_flags))
                self.assertIsNone(application.prompt_mode)
            finally:
                application.close_native_watch()


if __name__ == "__main__":
    unittest.main()
