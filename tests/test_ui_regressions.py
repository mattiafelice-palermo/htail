import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("htail_ui_test_module", str(ROOT / "htail"))
spec = importlib.util.spec_from_loader(loader.name, loader)
htail = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = htail
loader.exec_module(htail)

class UIRegressionTests(unittest.TestCase):
    def make_ui(self, service=None):
        return htail.TerminalUI(Path("example.md"), htail.SyntaxHighlighter(Path("example.md"), "none", False), htail.DisplayFilter(), False, 300, update_service=service)

    def test_content_width_reserves_last_terminal_column(self):
        ui = self.make_ui(); ui.dimensions = lambda: (120, 30)
        self.assertEqual(ui.content_width(), 119)

    def test_release_notes_are_categorized(self):
        features, fixes, other = htail.release_note_sections("## New features\n- Better modal\n\n## Bug fixes\n- Footer fix\n")
        self.assertEqual(features, ["Better modal"]); self.assertEqual(fixes, ["Footer fix"]); self.assertEqual(other, [])

    def test_update_panel_contains_categories_and_actions(self):
        ui = self.make_ui(htail.UpdateService("example/repo"))
        ui.update_release = htail.ReleaseInfo(version="0.7.4", tag="v0.7.4", asset_url="x", asset_name="htail", checksum_url="y", notes="## New features\n- Better modal\n\n## Bug fixes\n- Footer fix\n")
        visible = "\n".join(htail.strip_ansi(x) for x in ui._update_modal_lines(90, 25))
        self.assertIn("New features", visible); self.assertIn("Bug fixes", visible); self.assertIn("[Y] Update now", visible)

    def test_footer_advertises_manual_update_check(self):
        ui = self.make_ui(htail.UpdateService("example/repo")); ui.dimensions = lambda: (120, 30)
        self.assertIn("u check", "\n".join(ui._status_lines(119, 28)))
