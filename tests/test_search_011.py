from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from htail_app import app, core
from htail_app.app import MultiApp
from htail_app.pane import Pane
from htail_app.searching import SEARCH_BOOLEAN, SEARCH_REGEX, SEARCH_SIMPLE, compile_search, simple_pattern_to_regex


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
        self.assertIsNotNone(pane.search_regex.search("a.b literal"))
        self.assertIsNone(pane.search_regex.search("axb regex-like"))

        self.assertIsNone(pane.set_search("a.b", mode=SEARCH_REGEX))
        self.assertIsNotNone(pane.search_regex.search("a.b literal"))
        self.assertIsNotNone(pane.search_regex.search("axb regex-like"))

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

    def test_local_search_defaults_simple_and_tab_cycles_all_modes(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.handle_input("/")
                self.assertEqual(application.prompt_search_mode, SEARCH_SIMPLE)
                application.handle_input("TAB")
                self.assertEqual(application.prompt_search_mode, SEARCH_REGEX)
                application.handle_input("TAB")
                self.assertEqual(application.prompt_search_mode, SEARCH_BOOLEAN)
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
