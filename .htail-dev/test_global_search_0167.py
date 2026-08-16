from __future__ import annotations

from types import SimpleNamespace
import unittest

from htail_app.app import MultiApp
from htail_app.global_search import SORT_RELEVANCE, _preview_rows, render_global_search
from htail_app.input import normalize_plain_key, parse_escape_sequence
from htail_app.searching import GlobalSearchMatch, SEARCH_FUZZY, SEARCH_SIMPLE


class PreviewRenderer0167Tests(unittest.TestCase):
    def test_render_preview_wraps_long_selected_line_and_keeps_match_visible(self):
        text = "prefix " + ("context " * 10) + "reviewer " + ("tail " * 8)
        start = text.index("reviewer")
        result = GlobalSearchMatch(0, 3, "coord.md", text, start, start + len("reviewer"), 100.0)
        pane = SimpleNamespace(snapshot_raw=[f"line {i}\n" for i in range(3)] + [text + "\n"] + [f"line {i}\n" for i in range(4, 12)])

        rows = render_global_search(
            140,
            28,
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
        screen = "\n".join(rows)
        self.assertIn("PREVIEW · WRAP", screen)
        self.assertIn("reviewer", screen)
        # A continuation row has a blank line-number field and the same gutter.
        self.assertGreaterEqual(screen.count("│"), 10)

    def test_preview_context_scroll_moves_away_from_match(self):
        lines = [f"source-{i}\n" for i in range(30)]
        text = lines[10].rstrip("\n")
        result = GlobalSearchMatch(0, 10, "ctx.log", text, 0, len(text), None)
        pane = SimpleNamespace(snapshot_raw=lines)

        rows = _preview_rows([pane], result, rows=9, width=48, color=False, wrap=True, scroll=6)
        screen = "\n".join(rows)
        self.assertIn("source-16", screen)
        self.assertNotIn("> 10 │", screen)

    def test_nowrap_horizontal_scroll_exposes_other_context(self):
        text = "left-" + ("0123456789" * 8) + "-reviewer-right-tail"
        start = text.index("reviewer")
        result = GlobalSearchMatch(0, 0, "wide.log", text, start, start + len("reviewer"), 100.0)
        pane = SimpleNamespace(snapshot_raw=[text + "\n"])

        centered = "\n".join(_preview_rows([pane], result, rows=5, width=38, color=False, wrap=False, hscroll=0))
        shifted = "\n".join(_preview_rows([pane], result, rows=5, width=38, color=False, wrap=False, hscroll=-24))
        self.assertIn("reviewer", centered)
        self.assertNotEqual(centered, shifted)
        self.assertIn("…", shifted)


class PreviewControls0167Tests(unittest.TestCase):
    @staticmethod
    def make_app() -> MultiApp:
        application = MultiApp.__new__(MultiApp)
        lines = [f"line-{i} " + ("x" * 80) + "\n" for i in range(20)]
        selected_text = lines[8].rstrip("\n")
        match_start = selected_text.index("x") + 50
        first = GlobalSearchMatch(0, 8, "ctx.log", selected_text, match_start, match_start + 4, 100.0)
        second_text = lines[12].rstrip("\n")
        second_start = second_text.index("x") + 30
        second = GlobalSearchMatch(0, 12, "ctx.log", second_text, second_start, second_start + 4, 90.0)
        application.palette_active = False
        application.global_search_active = True
        application.global_search_results = [first, second]
        application.global_search_selected = 0
        application.global_search_preview = True
        application.global_search_preview_wrap = True
        application.global_search_preview_scroll = 0
        application.global_search_preview_hscroll = 0
        application._global_search_preview_result_key = None
        application.global_search_hit_regions = []
        application.global_search_sort = SORT_RELEVANCE
        application.panes = [SimpleNamespace(snapshot_raw=lines)]
        application.dirty = False
        application._refresh_global_search_results = lambda: None
        application.content_dimensions = lambda: (140, 28, 2)
        return application

    def test_input_decoder_exposes_preview_controls(self):
        self.assertEqual(normalize_plain_key("\x17"), "CTRL_W")
        self.assertEqual(parse_escape_sequence("\x1b[1;5A"), "CTRL_UP")
        self.assertEqual(parse_escape_sequence("\x1b[1;5B"), "CTRL_DOWN")
        self.assertEqual(parse_escape_sequence("\x1b[5;5~"), "CTRL_PAGEUP")
        self.assertEqual(parse_escape_sequence("\x1b[6;5~"), "CTRL_PAGEDOWN")

    def test_ctrl_scroll_and_wrap_toggle_do_not_change_result_selection(self):
        application = self.make_app()
        application._sync_global_search_preview_result()

        application.handle_input("CTRL_DOWN")
        self.assertEqual(application.global_search_selected, 0)
        self.assertEqual(application.global_search_preview_scroll, 1)

        application.handle_input("CTRL_W")
        self.assertFalse(application.global_search_preview_wrap)
        application.handle_input("RIGHT")
        self.assertEqual(application.global_search_selected, 0)
        self.assertNotEqual(application.global_search_preview_hscroll, 0)

    def test_selecting_new_result_recenters_preview(self):
        application = self.make_app()
        application._sync_global_search_preview_result()
        application.global_search_preview_scroll = 5
        application.global_search_preview_hscroll = 7
        application.global_search_selected = 1

        application._sync_global_search_preview_result()
        self.assertEqual(application.global_search_preview_scroll, 0)
        self.assertEqual(application.global_search_preview_hscroll, 0)

    def test_mouse_wheel_over_preview_scrolls_preview_not_results(self):
        application = self.make_app()
        application._sync_global_search_preview_result()
        application.global_search_hit_regions = [(80, 5, 130, 25, "preview", 0)]

        from htail_app.input import MouseEvent
        application._handle_global_search_mouse(MouseEvent(100, 10, "wheel_down"))
        self.assertEqual(application.global_search_selected, 0)
        self.assertEqual(application.global_search_preview_scroll, 3)


if __name__ == "__main__":
    unittest.main()
