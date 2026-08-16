from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from htail_app import app, core
from htail_app.input_accel import KeyBurst, _burst_movement, _coalesce_key_burst


class InputAccelerationTests(unittest.TestCase):
    def test_coalesces_identical_arrows_and_preserves_next_event(self):
        events = iter(["DOWN", "DOWN", "UP"])
        burst, pending = _coalesce_key_burst("DOWN", lambda: next(events, None))
        self.assertEqual(burst, KeyBurst("DOWN", 3))
        self.assertEqual(pending, "UP")

    def test_single_tap_stays_single_line_and_repeat_accelerates(self):
        self.assertEqual(_burst_movement(0, 1), 1)
        self.assertGreater(_burst_movement(4, 1), 1)
        self.assertLessEqual(_burst_movement(100, 100), 12)

    def test_arrow_burst_is_consumed_in_one_render_and_keeps_scroll_hint_total(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rows.log"
            path.write_text("".join(f"row {i}\n" for i in range(200)), encoding="utf-8")
            args = app.parse_args([str(path), "--layout", "rows", "--no-native-watch", "--no-color", "--no-self-install-prompt"])
            application = app.MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))
            application.dimensions = lambda: (100, 24)
            output = io.StringIO()
            try:
                with mock.patch("sys.stdout", output):
                    application.render()
                    output.seek(0); output.truncate(0)
                    application.handle_input(KeyBurst("UP", 8))
                    application.render()
                self.assertGreaterEqual(application.input_arrow_events_coalesced, 7)
                self.assertGreater(application.input_arrow_accelerated_rows, 0)
                self.assertEqual(application.terminal_scroll_region_uses, 1)
                self.assertNotIn(core.CLEAR_SCREEN, output.getvalue())
            finally:
                application.close_native_watch()


if __name__ == "__main__":
    unittest.main()
