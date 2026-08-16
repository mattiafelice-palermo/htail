from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest

from htail_app import app, core
from htail_app.app import MultiApp
from htail_app.global_search import (
    CorpusLine,
    SORT_FILE,
    SORT_RELEVANCE,
    fuzzy_backend,
    render_global_search,
    search_corpus,
)
from htail_app.input import normalize_plain_key
from htail_app.searching import GlobalSearchMatch, SEARCH_FUZZY, SEARCH_SIMPLE


RAPIDFUZZ_AVAILABLE = fuzzy_backend() != "unavailable"


@unittest.skipUnless(RAPIDFUZZ_AVAILABLE, "RapidFuzz not installed in source-test environment")
class FuzzySearchEngineTests(unittest.TestCase):
    def corpus(self):
        return [
            CorpusLine(0, 0, "one.log", "verify the parser after the run"),
            CorpusLine(1, 0, "two.md", "verification record"),
            CorpusLine(2, 0, "three.json", "verifiction recrod"),
            CorpusLine(0, 1, "one.log", "completely unrelated text"),
        ]

    def test_relevance_sort_is_global_across_files(self):
        page = search_corpus(
            self.corpus(),
            "verification record",
            SEARCH_FUZZY,
            re.IGNORECASE,
            file_filter=None,
            sort_mode=SORT_RELEVANCE,
            limit=20,
        )
        self.assertIsNone(page.error)
        self.assertGreaterEqual(len(page.results), 2)
        self.assertEqual(page.results[0].pane_name, "two.md")
        self.assertEqual(page.results[0].score, 100.0)
        scores = [result.score for result in page.results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_file_sort_groups_files_but_orders_groups_by_best_score(self):
        corpus = [
            CorpusLine(0, 0, "a.log", "verification almost here"),
            CorpusLine(1, 0, "b.log", "verification record"),
            CorpusLine(0, 1, "a.log", "verification evidence"),
            CorpusLine(1, 1, "b.log", "verify record later"),
        ]
        page = search_corpus(
            corpus,
            "verification record",
            SEARCH_FUZZY,
            0,
            file_filter=None,
            sort_mode=SORT_FILE,
            limit=20,
        )
        panes = [result.pane_index for result in page.results]
        self.assertEqual(panes[0], 1)
        self.assertEqual(panes, sorted(panes, key=lambda p: 0 if p == 1 else 1))

    def test_file_filter_limits_candidate_corpus(self):
        page = search_corpus(
            self.corpus(),
            "verification",
            SEARCH_FUZZY,
            re.IGNORECASE,
            file_filter=2,
            sort_mode=SORT_RELEVANCE,
            limit=20,
        )
        self.assertTrue(page.results)
        self.assertEqual({result.pane_index for result in page.results}, {2})


class GlobalSearchRendererTests(unittest.TestCase):
    class Pane:
        def __init__(self, lines):
            self.snapshot_raw = lines

    def test_wide_layout_has_results_and_preview_columns(self):
        panes = [self.Pane(["before\n", "verification record\n", "after\n"])]
        result = GlobalSearchMatch(0, 1, "reviewer.md", "verification record", 0, 12, 100.0)
        rows = render_global_search(
            140,
            32,
            query="verification",
            mode=SEARCH_FUZZY,
            mode_labels=((SEARCH_SIMPLE, "Simple"), (SEARCH_FUZZY, "Fuzzy")),
            ignore_case=True,
            sort_mode=SORT_RELEVANCE,
            file_filter_label="[All files]",
            results=[result],
            selected=0,
            truncated=False,
            error=None,
            panes=panes,
            preview_enabled=True,
            color=False,
        )
        screen = "\n".join(rows)
        self.assertIn("Global search", screen)
        self.assertIn("RESULTS — best matches", screen)
        self.assertIn("PREVIEW", screen)
        self.assertIn("Sort: [Relevance]", screen)
        self.assertIn("Files: [All files]", screen)

    def test_narrow_layout_drops_preview_instead_of_squeezing_it(self):
        rows = render_global_search(
            80,
            24,
            query="abc",
            mode=SEARCH_SIMPLE,
            mode_labels=((SEARCH_SIMPLE, "Simple"), (SEARCH_FUZZY, "Fuzzy")),
            ignore_case=False,
            sort_mode=SORT_FILE,
            file_filter_label="[All files]",
            results=[],
            selected=0,
            truncated=False,
            error=None,
            panes=[],
            preview_enabled=True,
            color=False,
        )
        screen = "\n".join(rows)
        self.assertIn("RESULTS — grouped by file", screen)
        self.assertNotIn("PREVIEW", screen)


@unittest.skipUnless(RAPIDFUZZ_AVAILABLE, "RapidFuzz not installed in source-test environment")
class GlobalSearchInteractionTests(unittest.TestCase):
    def make_app(self, root: Path):
        a = root / "a.txt"
        b = root / "b.txt"
        a.write_text("verification almost here\nnoise\n", encoding="utf-8")
        b.write_text("noise\nverification record\n", encoding="utf-8")
        args = app.parse_args([str(a), str(b), "--no-native-watch", "--no-color"])
        return MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))

    def test_global_mode_cycles_to_fuzzy_and_defaults_to_relevance(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.handle_input("g")
                application.handle_input("TAB")
                application.handle_input("TAB")
                application.handle_input("TAB")
                self.assertEqual(application.global_search_mode, SEARCH_FUZZY)
                self.assertEqual(application.global_search_sort, SORT_RELEVANCE)
                for ch in "verification record":
                    application.handle_input(ch)
                self.assertTrue(application.global_search_results)
                self.assertEqual(application.global_search_results[0].pane_index, 1)
                self.assertEqual(application.global_search_results[0].score, 100.0)
            finally:
                application.close_native_watch()

    def test_controls_toggle_sort_case_file_filter_and_preview(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.handle_input("g")
                for _ in range(3):
                    application.handle_input("TAB")
                original_case = application.global_search_ignore_case
                original_preview = application.global_search_preview
                application.handle_input("CTRL_T")
                self.assertNotEqual(application.global_search_ignore_case, original_case)
                application.handle_input("CTRL_O")
                self.assertEqual(application.global_search_sort, SORT_FILE)
                application.handle_input("CTRL_F")
                self.assertEqual(application.global_search_file_filter, 0)
                application.handle_input("CTRL_P")
                self.assertNotEqual(application.global_search_preview, original_preview)
            finally:
                application.close_native_watch()

    def test_fuzzy_enter_jumps_and_commits_a_simple_fragment_search(self):
        with tempfile.TemporaryDirectory() as td:
            application = self.make_app(Path(td))
            try:
                application.handle_input("g")
                for _ in range(3):
                    application.handle_input("TAB")
                for ch in "verification record":
                    application.handle_input(ch)
                application.handle_input("\r")
                self.assertFalse(application.global_search_active)
                self.assertEqual(application.focus, 1)
                self.assertEqual(application.panes[1].search_mode, SEARCH_SIMPLE)
                self.assertEqual(application.panes[1]._search_last_target, 1)
            finally:
                application.close_native_watch()


class GlobalSearchControlKeyTests(unittest.TestCase):
    def test_control_keys_are_normalized(self):
        self.assertEqual(normalize_plain_key("\x06"), "CTRL_F")
        self.assertEqual(normalize_plain_key("\x0f"), "CTRL_O")
        self.assertEqual(normalize_plain_key("\x10"), "CTRL_P")
        self.assertEqual(normalize_plain_key("\x14"), "CTRL_T")


if __name__ == "__main__":
    unittest.main()
