import unittest

from htail_app import app, core


class ModalRenderingTests(unittest.TestCase):
    def test_panel_top_border_has_exact_width_and_closed_corners(self):
        rows = app._panel_lines("Layout", ["hello"], 80, 20, False)
        visible = [core.strip_ansi(row) for row in rows]
        panel_rows = [row for row in visible if "Layout" in row]
        self.assertEqual(len(panel_rows), 1)
        row = panel_rows[0]
        self.assertEqual(len(row), 80)
        stripped = row.strip()
        self.assertTrue(stripped.startswith("╭"))
        self.assertTrue(stripped.endswith("╮"))
        self.assertIn(" Layout ", stripped)

    def test_modal_overlay_preserves_background_outside_panel(self):
        width, height = 80, 20
        background = [(f"background row {i}" + " " * width)[:width] for i in range(height)]
        panel = app._panel_lines("Help", ["inside modal"], width, height, False)
        overlay = app._overlay_modal(background, panel, width, height, False)
        self.assertEqual(len(overlay), height)
        self.assertIn("background row 0", core.strip_ansi(overlay[0]))
        self.assertTrue(any("inside modal" in core.strip_ansi(row) for row in overlay))

    def test_no_color_panel_contains_no_ansi_sequences(self):
        rows = app._panel_lines("Layout", ["hello"], 80, 20, False)
        self.assertTrue(all("\x1b[" not in row for row in rows))

    def test_dim_line_neutralizes_embedded_bright_styles(self):
        bright = core.paint("bright background", core.BOLD_LIGHT_CYAN, True)
        dimmed = app._dim_line(bright, True)
        self.assertIn(core.DIM, dimmed)
        self.assertNotIn(core.BOLD_LIGHT_CYAN, dimmed)
        self.assertEqual(core.strip_ansi(dimmed), "bright background")



if __name__ == "__main__":
    unittest.main()
