from __future__ import annotations

from pathlib import Path
import io
import os
import re
import tempfile
import time
import unittest
from types import SimpleNamespace

from htail_app import core
from htail_app import app
from htail_app.app import MultiApp, _changed_frame_rows
from htail_app.fsnotify import FsEvents, NativeWatchHub
from htail_app.globwatch import DynamicGlob, glob_root, has_magic
from htail_app.pane import Pane
from htail_app.watcher import FileFollower, WatchUpdate


def follower_args(**overrides):
    base = dict(
        encoding="utf-8",
        lines=None,
        verify_interval=9999.0,
        debounce=0.0,
        max_debounce=0.0,
        notification_gated=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class DamageRenderingTests(unittest.TestCase):
    def test_damage_rows_only_include_changed_physical_rows(self):
        old = ["a", "b", "c", "d"]
        new = ["a", "B", "c", "d"]
        self.assertEqual(_changed_frame_rows(old, new), [1])
        self.assertEqual(_changed_frame_rows(None, new), [0, 1, 2, 3])
        self.assertEqual(_changed_frame_rows(["x"], new), [0, 1, 2, 3])


class NativeNotificationTests(unittest.TestCase):
    def test_notification_gating_eliminates_idle_stat_probes_but_notify_wakes_follower(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.log"
            path.write_text("one\n", encoding="utf-8")
            follower = FileFollower(path, follower_args())
            follower.initialize_if_available()
            baseline = follower.stat_probe_count
            now = time.monotonic()
            for i in range(100):
                self.assertIsNone(follower.poll(now + i * 0.0001))
            self.assertEqual(follower.stat_probe_count, baseline)

            path.write_text("one\ntwo\n", encoding="utf-8")
            follower.notify()
            follower.poll(now + 0.1)
            update = follower.poll(now + 0.101)
            self.assertIsInstance(update, WatchUpdate)
            self.assertEqual(update.added, 1)
            self.assertEqual(list(update.current_snapshot), ["one\n", "two\n"])

    def test_poll_mode_keeps_v090_probe_behavior_without_notification_hint(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.log"
            path.write_text("one\n", encoding="utf-8")
            follower = FileFollower(path, follower_args(notification_gated=False))
            follower.initialize_if_available()
            baseline = follower.stat_probe_count
            follower.poll(time.monotonic())
            self.assertGreater(follower.stat_probe_count, baseline)

    @unittest.skipUnless(os.name != "nt", "POSIX-specific inotify smoke test")
    def test_linux_native_backend_reports_write_when_available(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "event.log"
            path.write_text("a\n", encoding="utf-8")
            hub = NativeWatchHub(enabled=True)
            try:
                if hub.backend != "inotify":
                    self.skipTest("inotify unavailable")
                hub.add_file(path)
                path.write_text("b\n", encoding="utf-8")
                deadline = time.monotonic() + 1.0
                seen = False
                while time.monotonic() < deadline:
                    events = hub.poll()
                    normalized = Path(os.path.abspath(os.fspath(path)))
                    if normalized in {Path(os.path.abspath(os.fspath(p))) for p in events.paths}:
                        seen = True
                        break
                    time.sleep(0.01)
                self.assertTrue(seen)
            finally:
                hub.close()


class GlobTests(unittest.TestCase):
    def test_dynamic_glob_discovers_only_new_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "a.log"
            first.write_text("a", encoding="utf-8")
            tracker = DynamicGlob(str(root / "*.log"))
            self.assertEqual(tracker.scan(), [first])
            self.assertEqual(tracker.scan(), [])
            second = root / "b.log"
            second.write_text("b", encoding="utf-8")
            self.assertEqual(tracker.scan(), [second])

    def test_glob_helpers_recognize_pattern_and_watch_root(self):
        self.assertTrue(has_magic("logs/*.log"))
        root = glob_root("logs/*.log")
        self.assertEqual(root.name, "logs")

    def test_app_adds_new_glob_match_without_restart(self):
        with tempfile.TemporaryDirectory() as td:
            pattern = str(Path(td) / "*.log")
            args = app.parse_args(["--glob", pattern, "--no-native-watch", "--no-color"])
            application = MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))
            try:
                self.assertEqual(len(application.panes), 0)
                created = Path(td) / "new.log"
                created.write_text("hello\n", encoding="utf-8")
                application._refresh_globs(time.monotonic() + 3.0, FsEvents(set(), set()))
                self.assertEqual([pane.name for pane in application.panes], ["new.log"])
                self.assertEqual(application.panes[0].snapshot_raw, ["hello\n"])
            finally:
                application.close_native_watch()

    def test_overlapping_initial_globs_do_not_duplicate_panes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "one.log"
            target.write_text("x\n", encoding="utf-8")
            args = app.parse_args([
                "--glob", str(root / "*.log"),
                "--glob", str(root / "one.*"),
                "--no-native-watch", "--no-color",
            ])
            application = MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))
            try:
                self.assertEqual([pane.name for pane in application.panes], ["one.log"])
            finally:
                application.close_native_watch()

    def test_noninteractive_glob_includes_existing_match(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "one.log"
            target.write_text("hello from glob\n", encoding="utf-8")
            args = app.parse_args(["--glob", str(root / "*.log"), "--no-start-banner", "--no-color"])
            # Avoid entering the follow loop: a pid known to be absent exits at
            # its first iteration, after initial glob content has been printed.
            args.pid = 999999999
            out = io.StringIO()
            old_stdout = app.sys.stdout
            try:
                app.sys.stdout = out
                app.run_noninteractive(args, False, core.DisplayFilter())
            finally:
                app.sys.stdout = old_stdout
            self.assertIn("hello from glob", out.getvalue())


class RegexInteractionTests(unittest.TestCase):
    def make_pane(self, color=True):
        highlighter = core.SyntaxHighlighter(Path("x.txt"), "none", color)
        pane = Pane(Path("x.txt"), highlighter, core.DisplayFilter(), color, 0.0)
        rows = ["alpha\n", "error one\n", "middle\n", "error two\n", "omega\n"]
        pane.add_initial(rows)
        pane.set_snapshot(rows)
        return pane

    def test_regex_search_wraps_and_navigates_snapshot(self):
        pane = self.make_pane(False)
        self.assertIsNone(pane.set_search(r"error"))
        self.assertTrue(pane.search_next(False, 40, 3))
        first = pane._snapshot_top
        self.assertTrue(pane.search_next(False, 40, 3))
        second = pane._snapshot_top
        self.assertGreater(second, first)
        self.assertTrue(pane.search_next(False, 40, 3))
        self.assertLessEqual(pane._snapshot_top, first)
        self.assertTrue(pane.search_next(True, 40, 3))
        self.assertGreaterEqual(pane._snapshot_top, second - 1)

    def test_regex_highlight_preserves_existing_ansi_and_can_be_cleared(self):
        pane = self.make_pane(True)
        self.assertIsNone(pane.set_highlight(r"error"))
        pane.prefer_snapshot = True
        pane._ensure_snapshot_layout(50)
        joined = "\n".join(pane._snapshot_visual_lines)
        self.assertIn("\x1b[4m", joined)
        self.assertIn("\x1b[24m", joined)
        pane.clear_highlight()
        pane._ensure_snapshot_layout(50)
        self.assertNotIn("\x1b[4m", "\n".join(pane._snapshot_visual_lines))

    def test_invalid_regex_is_rejected_without_destroying_previous_search(self):
        pane = self.make_pane(False)
        pane.set_search("error")
        error = pane.set_search("[")
        self.assertIsNotNone(error)
        self.assertEqual(pane.search_pattern, "error")


class UpdateFlowTests(unittest.TestCase):
    def test_manual_u_refresh_opens_modal_as_soon_as_release_arrives(self):
        class Service:
            enabled = True
            repo = "owner/repo"
            def snapshot(self):
                release = core.ReleaseInfo("9.9.9", "v9.9.9", "asset", "htail")
                return True, release, None
            def refresh(self):
                return True

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.txt"
            path.write_text("x\n", encoding="utf-8")
            args = app.parse_args([str(path), "--no-native-watch", "--no-color"])
            application = MultiApp(args, False, core.DisplayFilter(), Service())
            try:
                application.update_manual_check_pending = True
                application.update_check_done = False
                application._tick_updates(time.monotonic())
                self.assertTrue(application.update_confirm_active)
                self.assertFalse(application.update_manual_check_pending)
            finally:
                application.close_native_watch()


if __name__ == "__main__":
    unittest.main()
