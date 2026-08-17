from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from htail_app import app, core
from htail_app.input import MouseEvent, parse_escape_sequence
from htail_app.layout import Rect
from htail_app.pane import Pane
from htail_app.ui_features import (
    BUILTIN_PALETTES,
    _directional_neighbor,
    _scrollbar_geometry,
    _selection_word,
)


class UIFeatures0170Tests(unittest.TestCase):
    def _pane(self, name: str = "demo.log", color: bool = False) -> Pane:
        return Pane(
            Path(name),
            core.SyntaxHighlighter(Path(name), "none", color),
            core.DisplayFilter(),
            color,
            0.0,
        )

    def _app(self, root: Path, count: int = 2, layout: str = "grid", color: bool = False) -> app.MultiApp:
        paths = []
        for index in range(count):
            path = root / f"pane-{index}.log"
            path.write_text("alpha beta\n" + "".join(f"row {line}\n" for line in range(40)), encoding="utf-8")
            paths.append(path)
        args = app.parse_args([
            *(str(path) for path in paths),
            "--layout", layout,
            "--no-native-watch",
            "--no-self-install-prompt",
            "--syntax", "none",
            *( ["--no-color"] if not color else [] ),
        ])
        application = app.MultiApp(args, color, core.DisplayFilter(), core.UpdateService(""))
        application.dimensions = lambda: (100, 24)
        return application

    def test_scrollbar_thumb_is_proportional_and_tracks_position(self):
        self.assertEqual(_scrollbar_geometry(4, 8, 0, 8), (0, 0))
        top_start, top_size = _scrollbar_geometry(100, 20, 0, 20)
        bottom_start, bottom_size = _scrollbar_geometry(100, 20, 80, 20)
        self.assertEqual(top_size, 4)
        self.assertEqual(bottom_size, 4)
        self.assertEqual(top_start, 0)
        self.assertEqual(bottom_start, 16)

    def test_pane_box_renders_scrollbar_thumb_in_right_border(self):
        pane = self._pane(color=True)
        pane.add_initial([f"row {i}\n" for i in range(100)])
        rows = [core.strip_ansi(row) for row in pane.render_box(40, 10, True, 0)]
        body_edge = [row[-1] for row in rows[1:-1]]
        self.assertIn("┃", body_edge)
        self.assertTrue(set(body_edge) <= {"│", "┃"})
        self.assertLess(body_edge.count("┃"), len(body_edge))

    def test_alt_arrow_sequences_decode_for_spatial_navigation(self):
        self.assertEqual(parse_escape_sequence("\x1b[1;3A"), "ALT_UP")
        self.assertEqual(parse_escape_sequence("\x1b[1;3B"), "ALT_DOWN")
        self.assertEqual(parse_escape_sequence("\x1b[1;3C"), "ALT_RIGHT")
        self.assertEqual(parse_escape_sequence("\x1b[1;3D"), "ALT_LEFT")

    def test_directional_navigation_prefers_overlapping_neighbor(self):
        rects = [
            (0, Rect(0, 0, 50, 10)),
            (1, Rect(50, 0, 50, 10)),
            (2, Rect(0, 10, 50, 10)),
            (3, Rect(50, 10, 50, 10)),
        ]
        self.assertEqual(_directional_neighbor(rects, 0, "right"), 1)
        self.assertEqual(_directional_neighbor(rects, 0, "down"), 2)
        self.assertEqual(_directional_neighbor(rects, 3, "left"), 2)
        self.assertEqual(_directional_neighbor(rects, 3, "up"), 1)

    def test_ctrl_w_closes_focused_pane_and_follower(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._app(Path(td), 2, "columns")
            try:
                closed = application.followers[0]
                with mock.patch.object(closed, "close", wraps=closed.close) as close:
                    application.focus = 0
                    application.handle_input("CTRL_W")
                    self.assertEqual(len(application.panes), 1)
                    self.assertEqual(len(application.followers), 1)
                    self.assertEqual(application.focus, 0)
                    close.assert_called_once()
            finally:
                application.close_native_watch()

    def test_alt_arrow_moves_focus_by_geometry(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._app(Path(td), 4, "grid")
            output = io.StringIO()
            try:
                with mock.patch("sys.stdout", output):
                    application.render()
                application.focus = 0
                application.handle_input("ALT_RIGHT")
                self.assertEqual(application.focus, 1)
                application.handle_input("ALT_DOWN")
                self.assertEqual(application.focus, 3)
            finally:
                application.close_native_watch()

    def test_double_click_selects_only_clicked_pane_and_copies_word(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._app(Path(td), 2, "columns")
            output = io.StringIO()
            try:
                with mock.patch("sys.stdout", output):
                    application.render()
                first_rect = next(rect for index, rect in application.last_rects if index == 0)
                # Jump to the first source line so the stable "alpha beta" row is visible.
                pane = application.panes[0]
                pane.scroll("HOME", max(1, first_rect.height - 2))
                application.dirty = True
                with mock.patch("sys.stdout", output):
                    application.render()
                event = MouseEvent(x=first_rect.x + 2, y=first_rect.y + 1, button="left", pressed=True)
                with mock.patch("htail_app.ui_features._osc52_copy") as copy:
                    application.handle_input(event)
                    application.handle_input(event)
                copy.assert_called_once_with("alpha")
                self.assertIsNotNone(application.panes[0]._mouse_selection)
                self.assertIsNone(application.panes[1]._mouse_selection)
            finally:
                application.close_native_watch()

    def test_word_selection_supports_paths_and_identifiers(self):
        text = "error src/foo-bar.py:reviewer next"
        start, end, selected = _selection_word(text, text.index("reviewer") + 2)
        self.assertEqual(selected, "src/foo-bar.py:reviewer")
        self.assertEqual(text[start:end], selected)

    def test_builtin_palettes_are_named_and_complete(self):
        self.assertIn("default", BUILTIN_PALETTES)
        expected = {
            "accent", "muted", "warning", "error", "success", "secondary",
            "selection_fg", "selection_bg", "scrollbar", "footer_fg", "footer_bg",
        }
        for palette in BUILTIN_PALETTES.values():
            self.assertEqual(set(palette), expected)
            self.assertTrue(all(0 <= value <= 255 for value in palette.values()))


if __name__ == "__main__":
    unittest.main()
