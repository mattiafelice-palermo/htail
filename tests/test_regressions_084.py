from pathlib import Path
import hashlib
import io
import os
import tempfile
import unittest
from unittest import mock

from htail_app import core
from htail_app.app import _CLIUpdateProgress, parse_args
from htail_app.input import InputReader, MouseEvent
from htail_app.pane import Pane
from htail_app.watcher import FileFollower


class TTYBuffer(io.StringIO):
    def isatty(self):
        return True


class FakeResponse:
    def __init__(self, payload: bytes, content_length=True):
        self.payload = payload
        self.offset = 0
        self.headers = {'Content-Length': str(len(payload))} if content_length else {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self.payload) - self.offset
        chunk = self.payload[self.offset:self.offset+n]
        self.offset += len(chunk)
        return chunk


class Htail084Regressions(unittest.TestCase):
    def make_pane(self):
        path = Path('example.md')
        return Pane(path, core.SyntaxHighlighter(path, 'none', False), core.DisplayFilter(), False, 300)

    def test_interactive_lines_default_is_geometry_driven(self):
        self.assertIsNone(parse_args(['example.md']).lines)

    def test_follower_with_no_line_limit_returns_full_initial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'log.txt'
            path.write_text(''.join(f'line {i}\n' for i in range(80)), encoding='utf-8')
            args = parse_args([str(path), '--no-self-install-prompt'])
            follower = FileFollower(path, args)
            notice = follower.initialize_if_available()
            self.assertEqual(len(notice.initial_tail), 80)
            self.assertEqual(notice.initial_tail[-1], 'line 79\n')

    def test_initial_view_opens_at_last_visual_screenful_and_home_recovers_start(self):
        pane = self.make_pane()
        pane.add_initial([f'line {i} with enough text to wrap somewhat\n' for i in range(20)])
        rows = [core.strip_ansi(row).rstrip() for row in pane.view_rows(24, 5)]
        self.assertTrue(any('line 19' in row for row in rows))
        self.assertFalse(any('line 0 ' in row for row in rows))
        title = core.strip_ansi(pane.title(0, 80, True, 5))
        self.assertIn('↑', title)
        pane.scroll('HOME', 5)
        rows = [core.strip_ansi(row).rstrip() for row in pane.view_rows(24, 5)]
        self.assertTrue(any('line 0 ' in row for row in rows))
        title = core.strip_ansi(pane.title(0, 80, True, 5))
        self.assertIn('↓', title)

    def test_posix_reader_parses_back_to_back_mouse_sequences_without_textio_buffering(self):
        read_fd, write_fd = os.pipe()
        try:
            reader = InputReader(mouse=True)
            reader.enabled = True
            reader._fd = read_fd
            os.write(write_fd, b'\x1b[<0;75;5M\x1b[<64;75;5M')
            first = reader._poll_posix()
            second = reader._poll_posix()
            self.assertEqual(first, MouseEvent(x=74, y=4, button='left', pressed=True))
            self.assertEqual(second, MouseEvent(x=74, y=4, button='wheel_up', pressed=True))
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_update_service_reports_download_progress_and_install_phases(self):
        content = b'#!/usr/bin/env python3\nHTAIL_VERSION = "9.9.9"\nprint("ok")\n'
        checksum = (hashlib.sha256(content).hexdigest() + '  htail\n').encode()
        release = core.ReleaseInfo('9.9.9', 'v9.9.9', 'https://example/htail', 'htail', 'https://example/sha')
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'htail'
            target.write_text('#!/usr/bin/env python3\nHTAIL_VERSION = "0.8.3"\n', encoding='utf-8')
            target.chmod(0o755)
            service = core.UpdateService('example/repo')
            with mock.patch('htail_app.core.urllib.request.urlopen', side_effect=[FakeResponse(content), FakeResponse(checksum)]):
                ok, _ = service.install(release, target, progress=lambda stage, current, total: events.append((stage, current, total)))
        self.assertTrue(ok)
        downloads = [event for event in events if event[0] == 'Downloading release…']
        self.assertGreaterEqual(len(downloads), 2)
        self.assertEqual(downloads[-1][1:], (len(content), len(content)))
        stages = [event[0] for event in events]
        self.assertIn('Verifying SHA-256 checksum…', stages)
        self.assertIn('Backing up current executable…', stages)
        self.assertIn('Installing update…', stages)

    def test_cli_progress_renders_bar_and_percentage(self):
        stream = TTYBuffer()
        progress = _CLIUpdateProgress(stream)
        progress('Downloading release…', 50, 100)
        progress('Downloading release…', 100, 100)
        progress.finish()
        rendered = stream.getvalue()
        self.assertIn('100.0%', rendered)
        self.assertIn('100/100 bytes', rendered)
        self.assertIn('█', rendered)


if __name__ == '__main__':
    unittest.main()
