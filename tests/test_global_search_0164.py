from __future__ import annotations

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
