from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from htail_app import core
from htail_app.app import MultiApp, parse_args
from htail_app.pane import Pane


class Htail085Regressions(unittest.TestCase):
    def make_pane(self):
        path = Path("example.md")
        return Pane(path, core.SyntaxHighlighter(path, "none", False), core.DisplayFilter(), False, 300)

    def test_scrolling_stops_at_last_full_viewport(self):
        pane = self.make_pane()
        pane.add_initial([f"line {i}\n" for i in range(20)])
        pane.view_rows(40, 5)
        pane.scroll("HOME", 5)
        for _ in range(100):
            pane.scroll("DOWN", 5)
        self.assertEqual(pane.top, 15)
        rows = [core.strip_ansi(row).rstrip() for row in pane.view_rows(40, 5)]
        self.assertEqual(rows, [f"line {i}" for i in range(15, 20)])

    def test_latest_update_scrolls_inside_full_current_snapshot(self):
        pane = self.make_pane()
        pane.add_initial(["OLD ONLY\n", "old middle\n", "old end\n"])
        header, _ = pane.add_update(1, [("replace", ["changed now\n"])], 0, 1, 1, None, False, False, 10.0)
        current = ["CURRENT START\n", "current two\n", "current three\n", "changed now\n", "current five\n", "CURRENT END\n"]
        pane.set_snapshot(current, [3], prefer=True, update_header=header)
        pane._snapshot_view_rows(30, 3)
        pane.scroll("HOME", 3)
        rows = [core.strip_ansi(row).rstrip() for row in pane._snapshot_view_rows(30, 3)]
        self.assertTrue(pane.prefer_snapshot)
        self.assertIn("CURRENT START", rows)
        self.assertFalse(any("OLD ONLY" in row for row in rows))
        pane.scroll("END", 3)
        rows = [core.strip_ansi(row).rstrip() for row in pane._snapshot_view_rows(30, 3)]
        self.assertTrue(any("CURRENT END" in row for row in rows))

    def test_latest_snapshot_contains_update_marker_and_change_gutter(self):
        pane = self.make_pane()
        pane.add_initial(["before\n"])
        header, _ = pane.add_update(1, [("replace", ["changed\n"])], 0, 1, 1, None, False, False, 10.0)
        pane.set_snapshot(["first\n", "changed\n", "last\n"], [1], prefer=True, update_header=header)
        rows = [core.strip_ansi(row).rstrip() for row in pane._snapshot_view_rows(80, 5)]
        joined = "\n".join(rows)
        self.assertIn("update 1", joined)
        self.assertIn("▌ changed", joined)

    def test_bottom_border_shows_more_below_and_clears_at_eof(self):
        pane = self.make_pane()
        pane.add_initial([f"line {i}\n" for i in range(20)])
        pane.render_box(50, 8, True, 0)
        pane.scroll("HOME", 6)
        rows = pane.render_box(50, 8, True, 0)
        bottom = core.strip_ansi(rows[-1])
        self.assertIn("↓", bottom)
        self.assertIn("more", bottom)
        pane.scroll("END", 6)
        rows = pane.render_box(50, 8, True, 0)
        self.assertNotIn("more", core.strip_ansi(rows[-1]))

    def test_install_modal_shows_overall_bar_without_download_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.md"
            path.write_text("a\n", encoding="utf-8")
            args = parse_args([str(path), "--no-color", "--no-self-install-prompt"])
            app = MultiApp(args, False, core.compile_display_filter(args), core.UpdateService("example/repo"))
            app.update_release = core.ReleaseInfo("9.9.9", "v9.9.9", "https://x/a", "htail", "https://x/s")
            app.update_confirm_active = True
            app.update_installing = True
            app.update_install_status = "Verifying SHA-256 checksum…"
            app.update_install_progress = None
            app.update_overall_progress = 0.80
            rendered = "\n".join(core.strip_ansi(row) for row in app._update_lines(90, 20))
            self.assertIn("80.0%", rendered)
            self.assertIn("█", rendered)

    def test_fast_success_waits_before_restart_and_reaches_100_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.md"
            path.write_text("a\n", encoding="utf-8")
            args = parse_args([str(path), "--no-color", "--no-self-install-prompt"])
            service = core.UpdateService("example/repo")
            app = MultiApp(args, False, core.compile_display_filter(args), service)
            release = core.ReleaseInfo("9.9.9", "v9.9.9", "https://x/a", "htail", "https://x/s")
            app.update_installing = True
            app.update_progress_started_at = time.monotonic()
            with mock.patch.object(service, "install", return_value=(True, "updated")):
                before = time.monotonic()
                app._install_worker(release)
            self.assertTrue(app.update_installing)
            self.assertEqual(app.update_overall_progress, 1.0)
            self.assertEqual(app.update_install_status, "Update complete — restarting…")
            self.assertIsNotNone(app.pending_restart_at)
            self.assertGreaterEqual(app.pending_restart_at, before + 0.30)


if __name__ == "__main__":
    unittest.main()
