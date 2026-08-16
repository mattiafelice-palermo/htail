from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from htail_app import app, core
from htail_app.pane import Pane


class RenderPerformanceTests(unittest.TestCase):
    def _app(self, root: Path):
        paths = []
        for index in range(3):
            path = root / f"pane-{index}.log"
            path.write_text("".join(f"row {line} pane {index}\n" for line in range(200)), encoding="utf-8")
            paths.append(path)
        args = app.parse_args([
            *(str(path) for path in paths),
            "--layout", "columns",
            "--no-native-watch",
            "--no-color",
            "--no-self-install-prompt",
        ])
        return app.MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))

    def test_scoped_scroll_reuses_unchanged_pane_boxes(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._app(Path(td))
            try:
                application.dimensions = lambda: (120, 30)
                width, height, _ = application.content_dimensions()
                application._pane_boxes(width, height)
                application.dirty = False
                hits_before = application.render_pane_cache_hits
                misses_before = application.render_pane_cache_misses

                with mock.patch.object(application.panes[1], "render_box", wraps=application.panes[1].render_box) as middle, mock.patch.object(
                    application.panes[2], "render_box", wraps=application.panes[2].render_box
                ) as right:
                    application.handle_input("UP")
                    application._pane_boxes(width, height)

                self.assertEqual(middle.call_count, 0)
                self.assertEqual(right.call_count, 0)
                self.assertGreaterEqual(application.render_pane_cache_hits - hits_before, 2)
                self.assertEqual(application.render_pane_cache_misses - misses_before, 1)
            finally:
                application.close_native_watch()

    def test_global_dirty_still_rebuilds_every_pane(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._app(Path(td))
            try:
                application.dimensions = lambda: (120, 30)
                width, height, _ = application.content_dimensions()
                application._pane_boxes(width, height)
                application.dirty = False
                application.dirty = True
                before = application.render_pane_cache_misses
                application._pane_boxes(width, height)
                self.assertEqual(application.render_pane_cache_misses - before, 3)
            finally:
                application.close_native_watch()

    def test_viewport_decoration_is_cached(self):
        pane = Pane(
            Path("sample.log"),
            core.SyntaxHighlighter(Path("sample.log"), "none", False),
            core.DisplayFilter(),
            False,
            300,
        )
        row = "https://example.invalid/a very ordinary row"
        with mock.patch("htail_app.pane.linkify_urls", wraps=__import__("htail_app.pane", fromlist=["linkify_urls"]).linkify_urls) as linkify:
            first = pane._viewport_row(row, 80)
            second = pane._viewport_row(row, 80)
        self.assertEqual(first, second)
        self.assertEqual(linkify.call_count, 1)
        self.assertEqual(pane.viewport_cache_hits, 1)
        self.assertEqual(pane.viewport_cache_misses, 1)

    def test_viewport_cache_key_tracks_horizontal_offset(self):
        pane = Pane(
            Path("sample.log"),
            core.SyntaxHighlighter(Path("sample.log"), "none", False),
            core.DisplayFilter(),
            False,
            300,
        )
        pane.wrap_enabled = False
        row = "0123456789abcdefghijklmnopqrstuvwxyz"
        first = pane._viewport_row(row, 10)
        pane.horizontal_offset = 5
        second = pane._viewport_row(row, 10)
        self.assertNotEqual(first, second)
        self.assertEqual(pane.viewport_cache_misses, 2)


if __name__ == "__main__":
    unittest.main()
