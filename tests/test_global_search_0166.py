from __future__ import annotations

from types import SimpleNamespace
import unittest

from htail_app.global_search import SORT_RELEVANCE, _preview_rows, render_global_search
from htail_app.searching import GlobalSearchMatch, SEARCH_FUZZY, SEARCH_SIMPLE


class GlobalSearch0166PreviewTests(unittest.TestCase):
    def test_selected_preview_line_auto_scrolls_to_match(self):
        text = "This file is the append-only handoff log between implementer and reviewer."
        match_start = text.index("reviewer")
        result = GlobalSearchMatch(0, 0, "045-agent-coordination.md", text, match_start, match_start + len("reviewer"), 100.0)
        pane = SimpleNamespace(snapshot_raw=[text + "\n"])

        rows = _preview_rows([pane], result, rows=5, width=42, color=False)
        selected = next(row for row in rows if row.startswith(">"))

        self.assertIn("reviewer", selected)
        self.assertIn("…", selected)

    def test_full_preview_keeps_far_right_match_visible(self):
        text = "This file is the append-only handoff log between implementer and reviewer."
        match_start = text.index("reviewer")
        result = GlobalSearchMatch(0, 0, "045-agent-coordination.md", text, match_start, match_start + len("reviewer"), 100.0)
        pane = SimpleNamespace(snapshot_raw=[text + "\n"])

        rows = render_global_search(
            140,
            24,
            query="reviewer",
            mode=SEARCH_FUZZY,
            mode_labels=((SEARCH_SIMPLE, "Simple"), (SEARCH_FUZZY, "Fuzzy")),
            ignore_case=False,
            sort_mode=SORT_RELEVANCE,
            file_filter_label="[All files]",
            results=[result],
            selected=0,
            truncated=False,
            error=None,
            panes=[pane],
            preview_enabled=True,
            color=False,
        )

        right_cells = [parts[-2] for row in rows if len(parts := row.split("│")) >= 4]
        self.assertTrue(any("reviewer" in cell for cell in right_cells))

    def test_tab_expansion_keeps_highlight_aligned(self):
        text = "prefix\t" + ("context " * 6) + "reviewer tail"
        match_start = text.index("reviewer")
        result = GlobalSearchMatch(0, 0, "tabs.txt", text, match_start, match_start + len("reviewer"), 100.0)
        pane = SimpleNamespace(snapshot_raw=[text + "\n"])

        rows = _preview_rows([pane], result, rows=5, width=44, color=True)
        screen = "\n".join(rows)

        self.assertIn("\x1b[1;30;48;5;208mreviewer", screen)


if __name__ == "__main__":
    unittest.main()
