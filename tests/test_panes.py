from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
