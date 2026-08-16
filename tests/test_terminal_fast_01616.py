from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from htail_app import app, core


class TerminalFastPathTests(unittest.TestCase):
    def _application(self, root: Path, panes: int, layout: str) -> app.MultiApp:
        paths = []
        for index in range(panes):
            path = root / f"pane-{index}.log"
            path.write_text("".join(f"row {line} pane {index}\n" for line in range(120)), encoding="utf-8")
            paths.append(path)
        args = app.parse_args([
            *(str(path) for path in paths),
            "--layout", layout,
            "--no-native-watch",
            "--no-color",
            "--no-self-install-prompt",
        ])
        application = app.MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))
        application.dimensions = lambda: (100, 24)
        return application

    def _prime(self, application: app.MultiApp, output: io.StringIO) -> None:
        with mock.patch("sys.stdout", output):
            application.render()
        output.seek(0)
        output.truncate(0)

    def test_columns_scroll_writes_only_active_pane_rectangle(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._application(Path(td), 2, "columns")
            output = io.StringIO()
            try:
                self._prime(application, output)
                with mock.patch("sys.stdout", output):
                    application.handle_input("UP")
                    application.render()
                data = output.getvalue()
                self.assertEqual(application.terminal_rect_fast_paths, 1)
                self.assertEqual(application.terminal_scroll_region_uses, 0)
                self.assertNotIn(core.CLEAR_SCREEN, data)
                # The active left pane starts at column 1; the right pane is not
                # rewritten by the scoped rectangular fast path.
                self.assertIn("\x1b[1;1H", data)
                self.assertNotIn("\x1b[1;51H", data)
                self.assertLess(application.terminal_fast_bytes_written, 100 * 22)
            finally:
                application.close_native_watch()

    def test_full_width_scroll_uses_terminal_scroll_region(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._application(Path(td), 1, "rows")
            output = io.StringIO()
            try:
                self._prime(application, output)
                with mock.patch("sys.stdout", output):
                    application.handle_input("UP")
                    application.render()
                data = output.getvalue()
                self.assertEqual(application.terminal_scroll_region_uses, 1)
                self.assertEqual(application.terminal_rect_fast_paths, 0)
                self.assertIn("\x1b[2;21r", data)
                self.assertIn("\x1b[1T", data)
                self.assertIn("\x1b[r", data)
                # One exposed body row plus title/bottom position indicators.
                self.assertLessEqual(application.terminal_fast_rows_written, 3)
            finally:
                application.close_native_watch()

    def test_mouse_wheel_scroll_region_uses_three_row_shift(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._application(Path(td), 1, "rows")
            output = io.StringIO()
            try:
                self._prime(application, output)
                from htail_app.input import MouseEvent
                with mock.patch("sys.stdout", output):
                    application.handle_input(MouseEvent(x=10, y=10, button="wheel_up"))
                    application.render()
                self.assertEqual(application.terminal_scroll_region_uses, 1)
                self.assertIn("\x1b[3T", output.getvalue())
            finally:
                application.close_native_watch()

    def test_scroll_region_falls_back_when_rows_are_not_equivalent(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._application(Path(td), 1, "rows")
            output = io.StringIO()
            try:
                self._prime(application, output)
                pane = application.active_pane()
                original = pane.render_box

                def changed_box(*args, **kwargs):
                    rows = original(*args, **kwargs)
                    if len(rows) > 5:
                        rows[4] = rows[4].replace("row", "changed", 1)
                    return rows

                with mock.patch.object(pane, "render_box", side_effect=changed_box), mock.patch("sys.stdout", output):
                    application.handle_input("UP")
                    application.render()
                self.assertEqual(application.terminal_scroll_region_uses, 0)
                self.assertEqual(application.terminal_rect_fast_paths, 1)
                self.assertNotIn("\x1b[1T", output.getvalue())
            finally:
                application.close_native_watch()

    def test_fast_scroll_keeps_frame_baseline_for_next_global_redraw(self):
        with tempfile.TemporaryDirectory() as td:
            application = self._application(Path(td), 1, "rows")
            output = io.StringIO()
            try:
                self._prime(application, output)
                with mock.patch("sys.stdout", output):
                    application.handle_input("UP")
                    application.render()
                    self.assertIsNotNone(application._last_frame)
                    output.seek(0); output.truncate(0)
                    application.set_message("global status changed")
                    application.render()
                self.assertNotIn(core.CLEAR_SCREEN, output.getvalue())
            finally:
                application.close_native_watch()


if __name__ == "__main__":
    unittest.main()
