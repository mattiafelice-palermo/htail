import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

from htail_app import core
from htail_app.app import _process_alive, parse_args
from htail_app.pane import Pane
from htail_app.sources import CommandFollower, StreamFollower
from htail_app.watcher import FileFollower, analyze_changes


def args(**overrides):
    base = dict(encoding='utf-8', lines=None, verify_interval=999.0, debounce=0.0, max_debounce=0.0)
    base.update(overrides)
    return SimpleNamespace(**base)


class DiffAnalysisTests(unittest.TestCase):
    def test_single_pass_matches_core_events_and_counts(self):
        cases = [
            (["a\n"], ["a\n", "b\n"]),
            (["a\n", "b\n"], ["a\n", "B\n", "b\n"]),
            (["a\n", "b\n", "c\n"], ["a\n", "B\n", "c\n"]),
            (["a\n", "b\n"], ["a\n"]),
        ]
        for old, new in cases:
            expected = core.compute_changes(old, new)
            analysis = analyze_changes(old, new)
            self.assertEqual((list(analysis.events), analysis.added, analysis.replaced, analysis.deleted), expected)

    def test_changed_indices_are_produced_by_same_analysis(self):
        analysis = analyze_changes(["a\n", "b\n", "c\n"], ["a\n", "B\n", "c\n"])
        self.assertEqual(list(analysis.changed_new_indices), [1])


class FastAppendTests(unittest.TestCase):
    def test_pure_append_uses_fast_path_without_full_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'log.txt'
            path.write_text('a\n', encoding='utf-8')
            follower = FileFollower(path, args())
            follower.initialize_if_available()
            path.write_text('a\nb\n', encoding='utf-8')
            follower.poll(time.monotonic())
            update = follower.poll(time.monotonic() + 0.001)
            self.assertIsNotNone(update)
            self.assertEqual(follower.fast_append_hits, 1)
            self.assertEqual(list(update.changed_new_indices), [1])
            self.assertEqual(list(follower.previous), ['a\n', 'b\n'])

    def test_append_completing_unterminated_line_preserves_old_identity(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'log.txt'
            path.write_text('a', encoding='utf-8')
            follower = FileFollower(path, args())
            follower.initialize_if_available()
            with path.open('a', encoding='utf-8') as handle:
                handle.write('\nb\n')
            follower.poll(time.monotonic())
            update = follower.poll(time.monotonic() + 0.001)
            self.assertEqual(follower.fast_append_hits, 1)
            self.assertEqual(list(follower.previous), ['a\n', 'b\n'])
            self.assertEqual(update.added, 1)
            self.assertEqual(update.replaced, 0)


class StreamSourceTests(unittest.TestCase):
    def test_stream_source_emits_appended_lines_and_eof(self):
        follower = StreamFollower(io.StringIO('one\ntwo\n'), args(), label='stdin')
        follower.initialize_if_available()
        deadline = time.time() + 1.0
        update = None
        ended = None
        while time.time() < deadline and ended is None:
            result = follower.poll()
            if result is not None and getattr(result, 'kind', None) == 'ended':
                ended = result
            elif result is not None and hasattr(result, 'events'):
                update = result
            time.sleep(0.005)
        self.assertIsNotNone(update)
        self.assertEqual(update.added, 2)
        self.assertIsNotNone(ended)
        self.assertTrue(follower.finished)

    def test_command_source_merges_output_and_reaches_finished_state(self):
        command = f'"{sys.executable}" -c "import sys; print(123); print(456, file=sys.stderr)"'
        follower = CommandFollower(command, args(), label='test-command')
        follower.initialize_if_available()
        collected = []
        deadline = time.time() + 3.0
        while time.time() < deadline and not follower.finished:
            result = follower.poll()
            if result is not None and hasattr(result, 'events'):
                for kind, lines in result.events:
                    if kind == 'add':
                        collected.extend(lines)
            time.sleep(0.01)
        follower.close()
        joined = ''.join(collected)
        self.assertIn('123', joined)
        self.assertIn('456', joined)
        self.assertTrue(follower.finished)

    def test_parser_accepts_repeatable_exec_and_pid(self):
        parsed = parse_args(['--exec', 'echo one', '--exec', 'echo two', '--pid', '123', 'x.log'])
        self.assertEqual(parsed.commands, ['echo one', 'echo two'])
        self.assertEqual(parsed.pid, 123)

    def test_current_process_is_reported_alive(self):
        self.assertTrue(_process_alive(os.getpid()))


class RenderCacheTests(unittest.TestCase):
    def test_markdown_snapshot_reuses_render_and_wrap_cache(self):
        highlighter = core.SyntaxHighlighter(Path('x.md'), 'markdown', True)
        pane = Pane(Path('x.md'), highlighter, core.DisplayFilter(), True, 300.0)
        rows = [f'- item {i}\n' for i in range(100)]
        pane.set_snapshot(rows, prefer=True)
        pane._ensure_snapshot_layout(40)
        first_render = len(pane._render_cache)
        first_wrap = len(pane._wrap_cache)
        rows2 = list(rows)
        rows2[-1] = '- changed\n'
        pane.set_snapshot(rows2, [99], prefer=True)
        pane._ensure_snapshot_layout(40)
        self.assertGreaterEqual(len(pane._render_cache), first_render)
        self.assertGreaterEqual(len(pane._wrap_cache), first_wrap)
