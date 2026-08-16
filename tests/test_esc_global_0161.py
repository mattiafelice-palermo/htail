from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from htail_app import app, core
from htail_app.app import MultiApp
from htail_app.input import InputReader, parse_escape_sequence


class EscapeReaderTests(unittest.TestCase):
    def test_lone_escape_never_performs_blocking_continuation_read(self):
        reader = InputReader()
        reader._fd = 123
        reader._input_ready = lambda timeout: False
        reader._read_byte = lambda: self.fail('unexpected read')
        self.assertEqual(parse_escape_sequence(reader._read_escape_sequence(b'\x1b')), 'ESC')


class ModalEscapeTests(unittest.TestCase):
    def make_app(self, root, color=False):
        source = Path(root) / 'coord.md'
        source.write_text('# Coordination\nWorkflow verification remains authoritative.\n', encoding='utf-8')
        argv = [str(source), '--no-native-watch']
        if not color:
            argv.append('--no-color')
        return MultiApp(app.parse_args(argv), color, core.DisplayFilter(), core.UpdateService(''))

    def test_escape_closes_all_dismissible_modals(self):
        with tempfile.TemporaryDirectory() as td:
            a = self.make_app(td)
            try:
                a.handle_input(':'); self.assertTrue(a.palette_active); a.handle_input('ESC'); self.assertFalse(a.palette_active)
                a.handle_input('g'); self.assertTrue(a.global_search_active); a.handle_input('ESC'); self.assertFalse(a.global_search_active)
                a.handle_input('/'); a.handle_input('ESC'); self.assertIsNone(a.prompt_mode)
                a.handle_input('h'); a.handle_input('ESC'); self.assertIsNone(a.prompt_mode)
                a.handle_input('l'); a.handle_input('ESC'); self.assertFalse(a.layout_menu)
                a.handle_input('?'); a.handle_input('ESC'); self.assertFalse(a.help_active)
                a.update_confirm_active = True; a.handle_input('ESC'); self.assertFalse(a.update_confirm_active)
            finally:
                a.close_native_watch()

    def test_typing_global_search_with_color_cannot_exit_viewer(self):
        with tempfile.TemporaryDirectory() as td:
            a = self.make_app(td, color=True)
            try:
                a.handle_input('g')
                a.handle_input('v')
                self.assertTrue(a.global_search_active)
                width, frame = a._frame_rows()
                self.assertGreater(width, 0)
                self.assertIn('Global search', '\n'.join(core.strip_ansi(row) for row in frame))
            finally:
                a.close_native_watch()


if __name__ == '__main__':
    unittest.main()
