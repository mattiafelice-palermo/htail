from __future__ import annotations

from argparse import Namespace
import builtins
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from htail_app import app, core
from htail_app.input import parse_escape_sequence
from htail_app.pane import Pane
from htail_app import syntax_features


syntax_features.install()


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class SyntaxFeatureTests(unittest.TestCase):
    def _state_patch(self, root: Path):
        config = root / "config"
        state = config / "state.json"
        return mock.patch.multiple(core, APP_CONFIG_DIR=config, APP_STATE_FILE=state)

    def tearDown(self) -> None:
        syntax_features._set_active_theme("default")

    def test_theme_option_persists_for_future_runs(self):
        with tempfile.TemporaryDirectory() as td, self._state_patch(Path(td)):
            first = app.parse_args(["--theme", "nord", "demo.py"])
            self.assertEqual(first.theme, "nord")
            saved = json.loads(core.APP_STATE_FILE.read_text(encoding="utf-8"))
            self.assertEqual(saved["syntax_theme"], "nord")
            second = app.parse_args(["demo.py"])
            self.assertEqual(second.theme, "nord")
            self.assertEqual(syntax_features.current_theme(), "nord")

    def test_declined_pygments_prompt_is_not_repeated(self):
        args = Namespace(syntax="auto", no_color=False, no_install_prompt=False)
        stdin = TTYBuffer()
        stdout = TTYBuffer()
        stderr = TTYBuffer()
        with tempfile.TemporaryDirectory() as td, self._state_patch(Path(td)), \
             mock.patch.object(core, "HAVE_PYGMENTS", False), \
             mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr), \
             mock.patch.object(builtins, "input", return_value="n") as answer:
            core.maybe_offer_pygments_install(args, True)
            core.maybe_offer_pygments_install(args, True)
            self.assertEqual(answer.call_count, 1)
            saved = json.loads(core.APP_STATE_FILE.read_text(encoding="utf-8"))
            self.assertEqual(saved["pygments_install_prompt_decision"], "declined")

    @unittest.skipUnless(core.HAVE_PYGMENTS, "Pygments is required")
    def test_auto_lexer_uses_pygments_filename_registry_for_python_and_shell(self):
        python = core.SyntaxHighlighter(Path("demo.py"), "auto", True)
        shell = core.SyntaxHighlighter(Path("demo.sh"), "auto", True)
        docker = core.SyntaxHighlighter(Path("Dockerfile"), "auto", True)
        self.assertEqual(python.mode, "pygments")
        self.assertEqual(shell.mode, "pygments")
        self.assertEqual(docker.mode, "pygments")
        self.assertIn("Python", python.syntax_name)
        self.assertTrue("Bash" in shell.syntax_name or "Shell" in shell.syntax_name)

    @unittest.skipUnless(core.HAVE_PYGMENTS, "Pygments is required")
    def test_selected_theme_is_used_by_code_and_markdown_formatters(self):
        from pygments.styles import get_style_by_name

        syntax_features._set_active_theme("monokai")
        highlighter = core.SyntaxHighlighter(Path("demo.py"), "auto", True)
        self.assertEqual(highlighter.formatter.style, get_style_by_name("monokai"))
        formatter = core.Terminal256Formatter()
        self.assertEqual(formatter.style, get_style_by_name("monokai"))


    def test_bundle_self_test_rejects_missing_pygments(self):
        stderr = io.StringIO()
        with mock.patch.object(core, "HAVE_PYGMENTS", False), mock.patch("sys.stderr", stderr):
            self.assertEqual(app.main(["--bundle-self-test"]), 1)
        self.assertIn("Pygments unavailable", stderr.getvalue())

    def test_release_runtime_contract_bundles_pygments_and_rejects_rapidfuzz_only_legacy_cache(self):
        import importlib.util

        requirements = (Path(__file__).resolve().parents[1] / "tools" / "bundle-requirements.txt").read_text(encoding="utf-8")
        self.assertIn("Pygments==2.20.0", requirements)
        build_path = Path(__file__).resolve().parents[1] / "tools" / "build_release.py"
        spec = importlib.util.spec_from_file_location("htail_build_release_01618", build_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        wrapper = module.build_wrapper("0.16.18", b"payload")
        self.assertIn('(candidate / "rapidfuzz").is_dir()', wrapper)
        self.assertIn('(candidate / "pygments").is_dir()', wrapper)

    def test_pageup_pagedown_escape_sequences_and_viewport_page_navigation(self):
        self.assertEqual(parse_escape_sequence("\x1b[5~"), "PAGEUP")
        self.assertEqual(parse_escape_sequence("\x1b[6~"), "PAGEDOWN")

        pane = Pane(Path("demo.log"), core.SyntaxHighlighter(Path("demo.log"), "none", False), core.DisplayFilter(None, None), False, 300.0)
        pane._visual_lines = [f"row {index}" for index in range(100)]
        pane._layout_dirty = False
        pane.top = 50
        pane.scroll("PAGEUP", 20)
        self.assertEqual(pane.top, 32)
        pane.scroll("PAGEDOWN", 20)
        self.assertEqual(pane.top, 50)


if __name__ == "__main__":
    unittest.main()
