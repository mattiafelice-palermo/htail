from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from htail_app import app, core
from htail_app.input import MOUSE_ENABLE, MouseEvent, parse_escape_sequence
from htail_app.input_accel import KeyBurst
from htail_app.layout import Rect
from htail_app.pane import Pane
from htail_app.ui_features import (
    BUILTIN_PALETTES,
    SCROLLBAR_STYLES,
    _apply_scrollbar_style,
    _directional_neighbor,
    _scrollbar_geometry,
    _selection_word,
    current_palette,
    current_scrollbar_style,
)
import htail_app.ui_features as ui_features


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

    def test_pane_box_renders_separate_rail_by_default(self):
        pane = self._pane(color=True)
        pane.add_initial([f"row {i}\n" for i in range(100)])
        rows = [core.strip_ansi(row) for row in pane.render_box(40, 10, True, 0)]
        self.assertEqual(current_scrollbar_style(), "rail")
        self.assertTrue(all(row[-1] == "│" for row in rows[1:-1]))
        rail = [row[-3] for row in rows[1:-1]]
        self.assertIn("█", rail)
        self.assertTrue(set(rail) <= {"│", "█"})
        self.assertTrue(all(row[-2] == " " for row in rows[1:-1]))

    def test_border_scrollbar_style_preserves_legacy_edge_thumb(self):
        pane = self._pane(color=True)
        pane.add_initial([f"row {i}\n" for i in range(100)])
        _apply_scrollbar_style("border", persist=False)
        try:
            rows = [core.strip_ansi(row) for row in pane.render_box(40, 10, True, 0)]
        finally:
            _apply_scrollbar_style("rail", persist=False)
        body_edge = [row[-1] for row in rows[1:-1]]
        self.assertIn("┃", body_edge)
        self.assertTrue(set(body_edge) <= {"│", "┃"})

    def test_scrollbar_accent_is_only_used_for_focused_pane(self):
        pane = self._pane(color=True)
        pane.add_initial([f"row {i}\n" for i in range(100)])
        focused = pane.render_box(40, 10, True, 0)
        inactive = pane.render_box(40, 10, False, 0)
        accent = f"38;5;{current_palette()['scrollbar']}"
        self.assertTrue(any(accent in row and "█" in core.strip_ansi(row) for row in focused))
        self.assertFalse(any(accent in row and "█" in core.strip_ansi(row) for row in inactive))

    def test_scrollbar_style_persists_and_invalid_values_fall_back_to_rail(self):
        saved = {}
        with mock.patch.object(ui_features, "_load_state", return_value={}), mock.patch.object(
            ui_features, "_save_state", side_effect=lambda state: saved.update(state)
        ):
            self.assertEqual(_apply_scrollbar_style("minimal", persist=True), "minimal")
            self.assertEqual(saved["scrollbar_style"], "minimal")
        self.assertEqual(_apply_scrollbar_style("not-a-style", persist=False), "rail")

    def test_off_scrollbar_style_preserves_full_pane_content_width(self):
        pane = self._pane(color=False)
        pane.add_initial(["x" * 60 + "\n"])
        _apply_scrollbar_style("off", persist=False)
        try:
            rows = [core.strip_ansi(row) for row in pane.render_box(40, 6, True, 0)]
        finally:
            _apply_scrollbar_style("rail", persist=False)
        self.assertTrue(all(len(row) == 40 for row in rows))
        self.assertNotIn("█", "".join(rows))
        self.assertNotIn("▐", "".join(rows))

    def test_alt_arrow_sequences_decode_for_spatial_navigation(self):
        self.assertEqual(parse_escape_sequence("\x1b[1;3A"), "ALT_UP")
        self.assertEqual(parse_escape_sequence("\x1b[1;3B"), "ALT_DOWN")
        self.assertEqual(parse_escape_sequence("\x1b[1;3C"), "ALT_RIGHT")
        self.assertEqual(parse_escape_sequence("\x1b[1;3D"), "ALT_LEFT")

    def test_mouse_button_motion_is_enabled_and_decoded(self):
        self.assertIn("?1002h", MOUSE_ENABLE)
        event = parse_escape_sequence("\x1b[<32;10;5M")
        self.assertIsInstance(event, MouseEvent)
        self.assertEqual(event.button, "left")
        self.assertTrue(event.pressed)
        self.assertTrue(event.motion)

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

    def test_grid_alt_arrow_uses_rectangular_damage_without_body_clear(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._app(Path(td), 4, "grid")
            initial = io.StringIO()
            moved = io.StringIO()
            try:
                with mock.patch("sys.stdout", initial):
                    application.render()
                application.focus = 0
                application._mark_panes_dirty((0,))
                with mock.patch("sys.stdout", io.StringIO()):
                    application.render()
                application.handle_input("ALT_RIGHT")
                with mock.patch("sys.stdout", moved):
                    application.render()
                # Only footer/status rows may use clear-line. Grid body damage is
                # written directly into the old/new pane rectangles.
                self.assertLessEqual(moved.getvalue().count("\x1b[2K"), 2)
                self.assertGreaterEqual(application.terminal_rect_fast_paths, 2)
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

    def test_double_click_drag_extends_highlight_and_copies_final_range(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._app(Path(td), 2, "columns", color=True)
            output = io.StringIO()
            try:
                with mock.patch("sys.stdout", output):
                    application.render()
                first_rect = next(rect for index, rect in application.last_rects if index == 0)
                pane = application.panes[0]
                pane.scroll("HOME", max(1, first_rect.height - 2))
                application.dirty = True
                with mock.patch("sys.stdout", output):
                    application.render()
                press = MouseEvent(first_rect.x + 2, first_rect.y + 1, "left", True)
                drag = MouseEvent(first_rect.x + 10, first_rect.y + 1, "left", True, True)
                release = MouseEvent(first_rect.x + 10, first_rect.y + 1, "left", False)
                with mock.patch("htail_app.ui_features._osc52_copy") as copy:
                    application.handle_input(press)
                    application.handle_input(press)
                    application.handle_input(drag)
                    application.handle_input(release)
                self.assertEqual(copy.call_args_list[-1], mock.call("alpha beta"))
                self.assertEqual(pane._mouse_selection.text, "alpha beta")
                self.assertIsNone(application.panes[1]._mouse_selection)
                selection_bg = f"48;5;{current_palette()['selection_bg']}"
                self.assertTrue(any(selection_bg in row for row in pane.render_box(first_rect.width, first_rect.height, True, 0)))
            finally:
                application.close_native_watch()

    def test_palette_editor_accepts_coalesced_arrow_input(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._app(Path(td), 1, "columns")
            try:
                application.palette_active = True
                application.palette_mode = "ui-theme-editor"
                application._ui_theme_draft = current_palette()
                application._ui_theme_draft_name = "test"
                application._ui_theme_field = 0
                application.handle_input(KeyBurst("DOWN", 3))
                self.assertEqual(application._ui_theme_field, 3)
            finally:
                application.close_native_watch()

    def test_command_palette_exposes_scrollbar_style_cycle(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._app(Path(td), 1, "columns")
            try:
                application.palette_mode = "commands"
                labels = [item.label for item in application._palette_all_items()]
                self.assertTrue(any(label.startswith("Scrollbar style: ") for label in labels))
                self.assertEqual(SCROLLBAR_STYLES, ("rail", "border", "minimal", "off"))
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
