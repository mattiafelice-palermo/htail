from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import tempfile
import time
import unittest

from htail_app.input import MouseEvent, parse_escape_sequence
from htail_app.layout import pane_rects, resolve_auto
from htail_app.watcher import FileFollower, WatchUpdate


class LayoutTests(unittest.TestCase):
    def test_columns_cover_available_width(self):
        rects = pane_rects("columns", 3, 101, 20)
        self.assertEqual(sum(r.width for r in rects), 101)
        self.assertTrue(all(r.height == 20 for r in rects))

    def test_rows_cover_available_height(self):
        rects = pane_rects("rows", 4, 80, 31)
        self.assertEqual(sum(r.height for r in rects), 31)
        self.assertTrue(all(r.width == 80 for r in rects))

    def test_grid_has_one_rect_per_file(self):
        rects = pane_rects("grid", 5, 120, 30)
        self.assertEqual(len(rects), 5)
        self.assertTrue(all(r.width > 0 and r.height > 0 for r in rects))

    def test_auto_prefers_columns_for_two_wide_panes(self):
        self.assertEqual(resolve_auto(2, 160, 30), "columns")
        self.assertEqual(resolve_auto(2, 70, 30), "rows")


class InputTests(unittest.TestCase):
    def test_sgr_click(self):
        event = parse_escape_sequence("\x1b[<0;12;7M")
        self.assertEqual(event, MouseEvent(x=11, y=6, button="left", pressed=True))

    def test_sgr_wheel(self):
        self.assertEqual(parse_escape_sequence("\x1b[<64;2;3M").button, "wheel_up")
        self.assertEqual(parse_escape_sequence("\x1b[<65;2;3M").button, "wheel_down")

    def test_shift_tab(self):
        self.assertEqual(parse_escape_sequence("\x1b[Z"), "SHIFT_TAB")


class WatcherTests(unittest.TestCase):
    def args(self):
        return Namespace(
            lines=50,
            encoding="utf-8",
            verify_interval=0.0,
            debounce=0.0,
            max_debounce=0.0,
        )

    def test_independent_follower_detects_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.log"
            path.write_text("one\n", encoding="utf-8")
            follower = FileFollower(path, self.args())
            notice = follower.initialize_if_available()
            self.assertEqual(notice.initial_tail, ["one\n"])
            path.write_text("one\ntwo\n", encoding="utf-8")
            now = time.monotonic()
            self.assertIsNone(follower.poll(now))  # first metadata observation enters debounce state
            update = follower.poll(now + 0.001)
            self.assertIsInstance(update, WatchUpdate)
            self.assertEqual(update.added, 1)


if __name__ == "__main__":
    unittest.main()
