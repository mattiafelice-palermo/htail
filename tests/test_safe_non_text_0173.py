from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from htail_app import app, core, terminal_cells
from htail_app.app import MultiApp, parse_args
from htail_app.pane import Pane
from htail_app.text_safety import (
    CLASSIFIER_SAMPLE_BYTES,
    inspect_bytes,
    inspect_file,
    sanitize_source_line,
)
from htail_app.watcher import FileFollower


class TextLikelihoodTests(unittest.TestCase):
    def test_normal_text_and_selected_utf16_are_accepted(self):
        self.assertFalse(inspect_bytes(b"plain ASCII\ntext\n", "utf-8").suspicious)
        self.assertFalse(inspect_bytes("café 世界\n".encode("utf-8"), "utf-8").suspicious)
        utf16 = "hello 世界\n".encode("utf-16")
        self.assertFalse(inspect_bytes(utf16, "utf-16").suspicious)

    def test_binary_pdf_content_is_suspicious_regardless_of_filename(self):
        payload = b"%PDF-1.7\n1 0 obj\n" + bytes(range(256)) * 16
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "document.pdf"
            renamed = Path(tmp) / "document.txt"
            pdf.write_bytes(payload)
            renamed.write_bytes(payload)
            first = inspect_file(pdf)
            second = inspect_file(renamed)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertTrue(first.suspicious)
        self.assertEqual(first.suspicious, second.suspicious)

    def test_classifier_reads_only_a_bounded_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.log"
            path.write_bytes(b"readable\n" * 10000 + b"\x00\xff" * 100000)
            inspection = inspect_file(path, sample_size=128)
        self.assertEqual(inspection.sample_bytes, 128)
        self.assertLessEqual(inspection.sample_bytes, CLASSIFIER_SAMPLE_BYTES)

    def test_file_follower_carries_a_suspicious_source_warning(self):
        args = Namespace(
            lines=10,
            encoding="utf-8",
            verify_interval=0.0,
            debounce=0.0,
            max_debounce=0.0,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "renamed.txt"
            path.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 8)
            notice = FileFollower(path, args).initialize_if_available()
        self.assertIsNotNone(notice)
        self.assertIsNotNone(notice.warning)
        self.assertIn("selected encoding", notice.warning)


class TerminalSafetyTests(unittest.TestCase):
    def test_controls_are_visible_and_not_emitted_as_terminal_controls(self):
        raw = (
            "prefix"
            + chr(0x1B)
            + "[2J"
            + chr(0x07)
            + chr(0x08)
            + chr(0x00)
            + chr(0x7F)
            + chr(0x81)
            + "suffix"
            + chr(0x0D)
            + "tail\n"
        )
        safe = sanitize_source_line(raw)
        rendered = core.render_initial_lines(
            [raw], core.SyntaxHighlighter(Path("controls.txt"), "none", False)
        )[0]
        self.assertNotIn(chr(0x1B), rendered)
        for control in (0x07, 0x08, 0x00, 0x7F, 0x81):
            self.assertNotIn(chr(control), rendered)
        self.assertIn("␛[2J", safe)
        self.assertIn("␍", safe)
        self.assertEqual(rendered, safe.rstrip("\r\n"))

    def test_interactive_confirmation_defaults_to_rejecting_a_suspicious_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binary.log"
            path.write_bytes(b"%PDF-1.7\n" + bytes(range(256)) * 8)
            args = Namespace(files=[path], encoding="utf-8")
            output = StringIO()
            with mock.patch("builtins.input", return_value=""), redirect_stderr(output), redirect_stdout(output):
                app._confirm_initial_local_sources(args)
        self.assertEqual(args.files, [])
        self.assertIn("not opening", output.getvalue())


class TerminalCellGeometryTests(unittest.TestCase):
    def make_pane(self, path: Path) -> Pane:
        return Pane(
            path,
            core.SyntaxHighlighter(path, "none", False),
            core.DisplayFilter(),
            False,
            300,
        )

    def test_known_unicode_and_ansi_widths_use_terminal_cells(self):
        self.assertEqual(terminal_cells.display_width("ASCII"), 5)
        self.assertEqual(terminal_cells.display_width("café"), 4)
        self.assertEqual(terminal_cells.display_width("e\u0301"), 1)
        self.assertEqual(terminal_cells.display_width("界"), 2)
        self.assertEqual(terminal_cells.display_width("🙂"), 2)
        styled = core.paint("A界e\u0301🙂", core.BOLD_LIGHT_CYAN, True)
        self.assertEqual(terminal_cells.display_width(styled), 6)
        self.assertEqual(terminal_cells.display_width(core.clip_ansi(styled, 6)), 6)

    def test_wide_source_keeps_one_pane_border_at_exact_width(self):
        pane = self.make_pane(Path("unicode.txt"))
        pane.add_initial(["A界e\u0301🙂 mixed text\n"])
        rows = [core.strip_ansi(row) for row in pane.render_box(32, 8, True, 0)]
        self.assertTrue(all(terminal_cells.display_width(row) == 32 for row in rows))
        self.assertTrue(all(row.endswith("│") for row in rows[1:-1]))

    def test_composed_columns_and_rows_remain_width_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "a.txt", Path(tmp) / "b.txt"]
            for path in paths:
                path.write_text("界🙂 e\u0301\n", encoding="utf-8")
            for layout in ("columns", "rows"):
                args = parse_args(
                    [str(paths[0]), str(paths[1]), "--layout", layout, "--no-color", "--no-self-install-prompt"]
                )
                display_filter = core.compile_display_filter(args)
                instance = MultiApp(args, False, display_filter, core.UpdateService("example/repo"))
                try:
                    rows = [core.strip_ansi(row) for row in instance._pane_boxes(80, 20)]
                finally:
                    instance.close_native_watch()
                self.assertTrue(all(terminal_cells.display_width(row) == 80 for row in rows))


if __name__ == "__main__":
    unittest.main()
