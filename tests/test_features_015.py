from __future__ import annotations

from argparse import Namespace
import gzip
from pathlib import Path
import re
import tempfile
import time
import unittest

from htail_app import app, core
from htail_app.extras import markdown_outline, parse_duration, parse_ssh_source
from htail_app.pane import Pane
from htail_app.searching import SEARCH_BOOLEAN, SEARCH_SIMPLE, compile_search
from htail_app.sources import CompressedFollower


class BooleanSearchTests(unittest.TestCase):
    def test_boolean_and_or_not_and_quotes(self):
        pattern, error = compile_search('ERROR AND (retry OR "connection lost") AND NOT harmless', SEARCH_BOOLEAN, re.I)
        self.assertIsNone(error)
        self.assertIsNotNone(pattern.search('error: retry connection'))
        self.assertIsNotNone(pattern.search('ERROR connection lost'))
        self.assertIsNone(pattern.search('ERROR retry harmless'))
        self.assertIsNone(pattern.search('retry only'))

    def test_implicit_and(self):
        pattern, error = compile_search('alpha beta', SEARCH_BOOLEAN)
        self.assertIsNone(error)
        self.assertIsNotNone(pattern.search('alpha xx beta'))
        self.assertIsNone(pattern.search('alpha only'))


class OutlineAndDurationTests(unittest.TestCase):
    def test_outline_ignores_fenced_headings(self):
        entries = markdown_outline(['# One\n', '```\n', '# fake\n', '```\n', '### Three\n'])
        self.assertEqual([(e.level, e.source_index, e.text) for e in entries], [(1, 0, 'One'), (3, 4, 'Three')])

    def test_duration_parser(self):
        self.assertEqual(parse_duration('5m'), 300.0)
        self.assertEqual(parse_duration('1.5h'), 5400.0)
        self.assertEqual(parse_duration('off'), 0.0)


class SourceTests(unittest.TestCase):
    def args(self, lines=None):
        return Namespace(encoding='utf-8', lines=lines)

    def test_compressed_gzip_is_static_initial_source(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'x.log.gz'
            with gzip.open(path, 'wt', encoding='utf-8') as handle:
                handle.write('one\ntwo\n')
            follower = CompressedFollower(path, self.args())
            notice = follower.initialize_if_available()
            self.assertEqual(notice.initial_tail, ['one\n', 'two\n'])
            ended = follower.poll()
            self.assertEqual(ended.kind, 'ended')
            self.assertTrue(follower.finished)

    def test_ssh_parser_uses_system_ssh_and_remote_tail(self):
        argv, label = parse_ssh_source('user@example.com:/var/log/app.log')
        self.assertEqual(argv[0], 'ssh')
        self.assertIn('user@example.com', argv)
        self.assertIn('tail -F -- /var/log/app.log', argv[-1])
        self.assertIn('/var/log/app.log', label)


class PaneFeatureTests(unittest.TestCase):
    def make_pane(self, color=False):
        path = Path('sample.md')
        pane = Pane(path, core.SyntaxHighlighter(path, 'none', color), core.DisplayFilter(), color, 0.0, heartbeat_seconds=2.0)
        rows = ['# Heading\n', 'alpha target https://example.com/very/long/path\n', 'beta target\n']
        pane.add_initial(rows)
        pane.set_snapshot(rows)
        return pane

    def test_line_numbers_and_nowrap_horizontal_scroll(self):
        pane = self.make_pane(False)
        pane.toggle_line_numbers()
        pane.toggle_wrap()
        pane.scroll_horizontal(8)
        box = '\n'.join(core.strip_ansi(row) for row in pane.render_box(120, 6, True, 0))
        self.assertTrue(pane.show_line_numbers)
        self.assertFalse(pane.wrap_enabled)
        self.assertIn('LN', box.splitlines()[0])
        self.assertIn('NOWRAP', box.splitlines()[0])
        self.assertEqual(pane.horizontal_offset, 8)
        pane.scroll_horizontal(10000, 30)
        active_rows = pane._snapshot_visual_lines if pane.prefer_snapshot else pane._visual_lines
        max_width = max((len(core.strip_ansi(row)) for row in active_rows), default=0)
        self.assertLessEqual(pane.horizontal_offset, max(0, max_width - 30))

    def test_rate_and_heartbeat_status(self):
        pane = self.make_pane(False)
        now = time.monotonic()
        pane.watch_started_monotonic = now - 5.0
        pane.record_activity(20, 4096, now)
        title = core.strip_ansi(pane.title(0, 120, True, 4))
        self.assertIn('L/s', title)
        pane._activity.clear()
        title = core.strip_ansi(pane.title(0, 120, True, 4))
        self.assertIn('LATE', title)

    def test_search_selected_reuses_current_match(self):
        pane = self.make_pane(False)
        pane.set_search('target', mode=SEARCH_SIMPLE)
        pane.select_search_match(0, 60, 4)
        self.assertEqual(pane.selected_search_text(), 'target')

    def test_linkified_url_is_zero_width_for_strip(self):
        pane = self.make_pane(True)
        rendered = '\n'.join(pane.render_box(100, 6, True, 0))
        self.assertIn('\x1b]8;;https://example.com/very/long/path', rendered)
        self.assertNotIn('\x1b]8;;', core.strip_ansi(rendered))
        clipped = core.clip_ansi('\x1b]8;;https://example.com\x1b\\https://example.com/long/path\x1b]8;;\x1b\\', 8)
        self.assertTrue(clipped.endswith('\x1b]8;;\x1b\\' + core.RESET))


class PaletteAndParserTests(unittest.TestCase):
    def test_parser_accepts_heartbeat_and_ssh(self):
        args = app.parse_args(['--heartbeat', '5m', '--ssh', 'host:/tmp/a.log'])
        self.assertEqual(args.heartbeat, 300.0)
        self.assertEqual(args.ssh_sources, ['host:/tmp/a.log'])

    def test_palette_contains_requested_actions(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'x.md'
            path.write_text('# Alpha\ntext\n## Beta\n', encoding='utf-8')
            args = app.parse_args([str(path), '--no-native-watch', '--no-color'])
            application = app.MultiApp(args, False, core.DisplayFilter(), core.UpdateService(''))
            try:
                application._open_palette()
                labels = [item.label for item in application.palette_items]
                self.assertIn('Markdown outline', labels)
                self.assertIn('Toggle wrap', labels)
                self.assertIn('Toggle line numbers', labels)
                outline = next(i for i, item in enumerate(application.palette_items) if item.action == 'outline')
                application.palette_selected = outline
                application._execute_palette_item()
                self.assertEqual(application.palette_mode, 'outline')
                self.assertTrue(any('Alpha' in item.label for item in application.palette_items))
            finally:
                application.close_native_watch()


if __name__ == '__main__':
    unittest.main()
