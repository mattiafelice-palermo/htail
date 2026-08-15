from pathlib import Path
import tempfile
import unittest
from unittest import mock

from htail_app import core
from htail_app.app import MultiApp, parse_args
from htail_app.input import MouseEvent
from htail_app.pane import Pane


class PaneTests(unittest.TestCase):
    def make_pane(self, path):
        return Pane(path, core.SyntaxHighlighter(path, "none", False), core.DisplayFilter(), False, 300)

    def test_paused_pane_counts_unseen_updates(self):
        pane = self.make_pane(Path("a.md"))
        pane.add_initial(["old\n"])
        pane.toggle_pause()
        pane.add_update(1, [("add", ["new\n"])], 1, 0, 0, None, False, False, 10.0)
        self.assertTrue(pane.paused)
        self.assertEqual(pane.unseen_updates, 1)
        pane.toggle_pause()
        self.assertFalse(pane.paused)
        self.assertEqual(pane.unseen_updates, 0)

    def test_layout_width_change_keeps_same_logical_top(self):
        pane = self.make_pane(Path("a.md"))
        pane.add_initial(["first line that wraps over several visual rows because it is intentionally long\n", "second\n", "third\n"])
        pane.view_rows(20, 4)
        pane.top = 1
        before = pane._logical_at_top()
        pane.view_rows(40, 4)
        self.assertEqual(pane._logical_at_top(), before)

    def test_short_snapshot_shows_whole_file_and_marks_changed_row(self):
        pane = self.make_pane(Path("short.md"))
        pane.add_initial(["old tail\n"])
        pane.add_update(1, [("replace", ["changed\n"])], 0, 1, 1, None, False, False, 10.0)
        pane.set_snapshot(["first\n", "second\n", "changed\n"], [2], prefer=True)
        body = pane._snapshot_view_rows(40, 6)
        plain = [core.strip_ansi(row).rstrip() for row in body]
        self.assertEqual(plain[:3], ["first", "second", "▌ changed"])

    def test_pane_top_border_closes_at_exact_right_edge(self):
        pane = self.make_pane(Path("a.md"))
        pane.add_initial(["hello\n"])
        rows = pane.render_box(40, 8, True, 0)
        visible = [core.strip_ansi(row) for row in rows]
        self.assertTrue(all(len(row) == 40 for row in visible))
        self.assertTrue(visible[0].endswith("╮"))
        self.assertTrue(visible[1].endswith("│"))



class MultiAppInteractionTests(unittest.TestCase):
    def test_layout_switch_and_mouse_focus_preserve_pane_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.md"; b = Path(tmp) / "b.md"
            a.write_text("a\n", encoding="utf-8"); b.write_text("b\n", encoding="utf-8")
            args = parse_args([str(a), str(b), "--layout", "rows", "--no-color", "--no-self-install-prompt"])
            filt = core.compile_display_filter(args)
            app = MultiApp(args, False, filt, core.UpdateService("example/repo"))
            app.panes[0].paused = True
            app.handle_input("l"); app.handle_input("c")
            self.assertEqual(app.layout, "columns")
            self.assertTrue(app.panes[0].paused)
            app._pane_boxes(100, 20)
            app.handle_input(MouseEvent(x=75, y=5, button="left", pressed=True))
            self.assertEqual(app.focus, 1)

    def test_mouse_release_does_not_change_focus(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.md"; b = Path(tmp) / "b.md"
            a.write_text("a\n", encoding="utf-8"); b.write_text("b\n", encoding="utf-8")
            args = parse_args([str(a), str(b), "--layout", "columns", "--no-color", "--no-self-install-prompt"])
            filt = core.compile_display_filter(args)
            app = MultiApp(args, False, filt, core.UpdateService("example/repo"))
            app._pane_boxes(100, 20)
            app.handle_input(MouseEvent(x=75, y=5, button="left", pressed=True))
            self.assertEqual(app.focus, 1)
            app.handle_input(MouseEvent(x=10, y=5, button="left", pressed=False))
            self.assertEqual(app.focus, 1)

    def test_confirmed_interactive_update_runs_worker_and_schedules_restart(self):
        class ImmediateThread:
            def __init__(self, target, args=(), **_kwargs):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.md"
            path.write_text("a\n", encoding="utf-8")
            args = parse_args([str(path), "--no-color", "--no-self-install-prompt"])
            filt = core.compile_display_filter(args)
            service = core.UpdateService("example/repo")
            app = MultiApp(args, False, filt, service)
            release = core.ReleaseInfo(
                version="9.9.9",
                tag="v9.9.9",
                asset_url="https://example.invalid/htail",
                asset_name="htail",
                checksum_url="https://example.invalid/htail.sha256",
            )
            app.update_release = release
            app.update_confirm_active = True

            with mock.patch("htail_app.app.threading.Thread", ImmediateThread), mock.patch.object(
                service, "install", return_value=(True, "updated htail")
            ) as install:
                # Regression: 0.8.1/0.8.2 raised AttributeError here because
                # MultiApp._install_worker did not exist.
                app.handle_input("y")

            install.assert_called_once()
            self.assertTrue(app.update_installing)
            self.assertEqual(app.update_install_result, (True, "updated htail"))
            self.assertEqual(app.update_overall_progress, 1.0)
            self.assertIsNotNone(app.pending_restart)
            self.assertIsNotNone(app.pending_restart_at)
            self.assertEqual(app.pending_restart[2], "updated htail")



if __name__ == "__main__":
    unittest.main()
