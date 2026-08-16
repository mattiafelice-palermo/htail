from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from htail_app import app, core
from htail_app.app import MultiApp
from htail_app.global_search import SORT_FILE, SORT_RELEVANCE, render_global_search
from htail_app.searching import (
    GlobalSearchMatch,
    SEARCH_BOOLEAN,
    SEARCH_FUZZY,
    SEARCH_REGEX,
    SEARCH_SIMPLE,
)


class GroupedRendererRegressionTests(unittest.TestCase):
    def test_non_fuzzy_grouped_result_has_no_score_attribute_error(self):
        result = GlobalSearchMatch(
            0, 0, "coord.md", "Workflow verification remains authoritative.", 9, 21, None
        )
        rows = render_global_search(
            120,
            28,
            query="verification",
            mode=SEARCH_SIMPLE,
            mode_labels=(
                (SEARCH_SIMPLE, "Simple"),
                (SEARCH_REGEX, "Regex"),
                (SEARCH_BOOLEAN, "Boolean"),
                (SEARCH_FUZZY, "Fuzzy"),
            ),
            ignore_case=True,
            sort_mode=SORT_FILE,
            file_filter_label="[All files]",
            results=[result],
            selected=0,
            truncated=False,
            error=None,
            panes=[],
            preview_enabled=False,
            color=True,
        )
        screen = "\n".join(core.strip_ansi(row) for row in rows)
        self.assertIn("coord.md", screen)
        self.assertIn("RESULTS — grouped by file", screen)
        self.assertNotIn("Search rendering error", screen)


class LiveGlobalSearchRenderMatrixTests(unittest.TestCase):
    def make_app(self, root: Path, color: bool) -> MultiApp:
        a = root / "coord.md"
        b = root / "reviewer.log"
        a.write_text(
            "Workflow verification remains authoritative.\n"
            "The verification record is complete.\n",
            encoding="utf-8",
        )
        b.write_text(
            "Reviewer verification passed.\n"
            "Final verification evidence recorded.\n",
            encoding="utf-8",
        )
        argv = [str(a), str(b), "--no-native-watch"]
        if not color:
            argv.append("--no-color")
        return MultiApp(app.parse_args(argv), color, core.DisplayFilter(), core.UpdateService(""))

    def render_clean(self, application: MultiApp, expected_heading: str | None = None) -> str:
        width, frame = application._frame_rows()
        self.assertGreater(width, 0)
        screen = "\n".join(core.strip_ansi(row) for row in frame)
        self.assertTrue(application.global_search_active)
        self.assertIn("Global search", screen)
        self.assertNotIn("Search rendering error", screen)
        self.assertNotIn("AttributeError", screen)
        self.assertNotIn("Traceback", screen)
        if expected_heading is not None:
            self.assertIn(expected_heading, screen)
        return screen

    def test_every_mode_renders_after_every_keystroke_and_control(self):
        cases = (
            (SEARCH_SIMPLE, 0, "verification", "RESULTS — grouped by file"),
            (SEARCH_REGEX, 1, "ver.*tion", "RESULTS — grouped by file"),
            (SEARCH_BOOLEAN, 2, "verification", "RESULTS — grouped by file"),
            (SEARCH_FUZZY, 3, "verifiction", "RESULTS — best matches"),
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for color in (False, True):
                for mode, tabs, query, heading in cases:
                    with self.subTest(color=color, mode=mode):
                        application = self.make_app(root, color)
                        try:
                            application.handle_input("g")
                            self.render_clean(application)
                            for _ in range(tabs):
                                application.handle_input("TAB")
                                self.render_clean(application)
                            self.assertEqual(application.global_search_mode, mode)
                            for ch in query:
                                application.handle_input(ch)
                                self.render_clean(application, heading)
                            self.assertTrue(application.global_search_results)

                            application.handle_input("DOWN")
                            self.render_clean(application, heading)
                            application.handle_input("CTRL_T")
                            self.render_clean(application, heading)
                            application.handle_input("CTRL_F")
                            self.render_clean(application, heading)
                            application.handle_input("CTRL_P")
                            self.render_clean(application, heading)

                            if mode == SEARCH_FUZZY:
                                self.assertEqual(application.global_search_sort, SORT_RELEVANCE)
                                application.handle_input("CTRL_O")
                                self.assertEqual(application.global_search_sort, SORT_FILE)
                                self.render_clean(application, "RESULTS — grouped by file")

                            application.handle_input("ESC")
                            self.assertFalse(application.global_search_active)
                        finally:
                            application.close_native_watch()


if __name__ == "__main__":
    unittest.main()
