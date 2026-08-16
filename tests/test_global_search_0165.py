from __future__ import annotations

import re
import unittest

from htail_app.global_search import (
    CorpusLine,
    SORT_RELEVANCE,
    fuzzy_backend,
    search_corpus,
)
from htail_app.searching import SEARCH_FUZZY


RAPIDFUZZ_AVAILABLE = fuzzy_backend() != "unavailable"


@unittest.skipUnless(RAPIDFUZZ_AVAILABLE, "RapidFuzz not installed in source-test environment")
class FuzzySubstringRanking0165Tests(unittest.TestCase):
    def test_exact_substring_outranks_shorter_approximate_match(self):
        corpus = [
            CorpusLine(0, 0, "short.md", "review clean"),
            CorpusLine(
                1,
                0,
                "long.md",
                "This is a deliberately much longer line with context before and after "
                "the exact reviewer token for regression testing",
            ),
        ]

        page = search_corpus(
            corpus,
            "reviewer",
            SEARCH_FUZZY,
            0,
            file_filter=None,
            sort_mode=SORT_RELEVANCE,
            limit=20,
        )

        self.assertIsNone(page.error)
        self.assertEqual([result.pane_name for result in page.results], ["long.md", "short.md"])
        self.assertEqual(page.results[0].score, 100.0)
        self.assertLess(page.results[1].score, page.results[0].score)
        exact = page.results[0]
        self.assertEqual(exact.text[exact.match_start:exact.match_end], "reviewer")

    def test_case_insensitive_alignment_preserves_original_span(self):
        text = "prefix with enough surrounding context REVIEWER and more trailing context"
        page = search_corpus(
            [CorpusLine(0, 0, "case.log", text)],
            "reviewer",
            SEARCH_FUZZY,
            re.IGNORECASE,
            file_filter=None,
            sort_mode=SORT_RELEVANCE,
            limit=20,
        )

        self.assertEqual(len(page.results), 1)
        result = page.results[0]
        self.assertEqual(result.score, 100.0)
        self.assertEqual(result.text[result.match_start:result.match_end], "REVIEWER")


if __name__ == "__main__":
    unittest.main()
