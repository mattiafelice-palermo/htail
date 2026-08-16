from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from htail_app import app, core
from htail_app.git_remote import GitFileContext, GitRemoteFollower, read_remote_snapshot
from htail_app.layout import pane_rects
from htail_app.pane import Pane

from tests.test_git_remote_0168 import GitRepoFixture, git


class RemotePaneIdentityTests(unittest.TestCase):
    def test_remote_source_is_visually_explicit_and_local_title_is_unchanged(self):
        pane = Pane(Path("status.md"), core.SyntaxHighlighter(Path("status.md"), "none", True), core.DisplayFilter(), True, 0)
        pane.add_initial(["hello\n"])
        local = pane.render_box(80, 5, True, 0)
        self.assertNotIn("REMOTE", core.strip_ansi(local[0]))

        pane.source_label = "origin/main"
        remote = pane.render_box(80, 5, True, 0)
        self.assertIn("REMOTE origin/main", core.strip_ansi(remote[0]))
        self.assertIn(core.BOLD_LIGHT_MAGENTA, remote[0])
        self.assertIn("\x1b[1;30;105m", remote[0])


class MarkdownTableTests(unittest.TestCase):
    def test_markdown_fenced_code_renders_through_app_extension(self):
        highlighter = core.SyntaxHighlighter(Path("code.md"), "markdown", True)
        rendered = [core.strip_ansi(row) for row in highlighter.render_lines([
            "```python\n",
            "print('hello')\n",
            "```\n",
        ])]
        self.assertEqual(len(rendered), 3)
        self.assertIn("print('hello')", rendered[1])

    def test_markdown_table_is_aligned_and_separator_is_rendered(self):
        highlighter = core.SyntaxHighlighter(Path("table.md"), "markdown", True)
        rendered = [core.strip_ansi(row) for row in highlighter.render_lines([
            "| Name | Count |\n",
            "| :--- | ---: |\n",
            "| alpha | 12 |\n",
            "| longer name | 3 |\n",
        ])]
        self.assertEqual(len(rendered), 4)
        self.assertTrue(rendered[0].startswith("│ "))
        self.assertTrue(rendered[0].endswith(" │"))
        self.assertTrue(rendered[1].startswith("├"))
        self.assertTrue(rendered[1].endswith("┤"))
        self.assertIn("longer name", rendered[3])
        count_column = rendered[2].split("│")[-2]
        self.assertTrue(count_column.endswith("12 "))

    def test_table_rows_never_wrap_but_can_scroll_horizontally(self):
        highlighter = core.SyntaxHighlighter(Path("table.md"), "markdown", True)
        pane = Pane(Path("table.md"), highlighter, core.DisplayFilter(), True, 0)
        raw = [
            "| Name | Description |\n",
            "| --- | --- |\n",
            "| alpha | this is deliberately much wider than the pane viewport |\n",
        ]
        pane.add_initial(raw)
        pane.set_snapshot(raw)
        pane._ensure_layout(24)
        self.assertEqual(len(pane._visual_lines), 3)
        self.assertTrue(pane.wrap_enabled)
        pane.scroll_horizontal(8, 24)
        self.assertGreater(pane.horizontal_offset, 0)
        self.assertIn("TABLE ↔", core.strip_ansi(pane.title(0, 100, True)))


class GitRemoteFetchEfficiencyTests(unittest.TestCase):
    def test_picker_sha_avoids_duplicate_remote_query_and_fetch_when_objects_exist(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = GitRepoFixture(Path(td))
            context = app.discover_git_file(fixture.file)
            self.assertIsNotNone(context)
            sha = git(fixture.work, "rev-parse", "HEAD")
            with mock.patch("htail_app.git_remote.remote_head_sha", side_effect=AssertionError("duplicate ls-remote")), mock.patch(
                "htail_app.git_remote._fetch_branch", side_effect=AssertionError("unnecessary fetch")
            ):
                actual_sha, lines = read_remote_snapshot(
                    context,
                    "origin",
                    "main",
                    "utf-8",
                    expected_sha=sha,
                )
            self.assertEqual(actual_sha, sha)
            self.assertEqual(lines, ["remote one\n"])

    def test_missing_objects_use_private_blobless_htail_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture = GitRepoFixture(root)
            consumer = root / "consumer"
            cache = root / "htail-cache"
            consumer.mkdir()
            git(consumer, "init")
            git(consumer, "remote", "add", "origin", fixture.remote.as_uri())
            context = GitFileContext(consumer, "docs/status.md", ("origin",))
            sha = git(fixture.work, "rev-parse", "HEAD")

            with mock.patch.dict(os.environ, {"HTAIL_GIT_CACHE_DIR": str(cache)}):
                actual_sha, lines = read_remote_snapshot(
                    context,
                    "origin",
                    "main",
                    "utf-8",
                    expected_sha=sha,
                )
            self.assertEqual(actual_sha, sha)
            self.assertEqual(lines, ["remote one\n"])
            user_refs = git(consumer, "for-each-ref", "--format=%(refname)", "refs/htail")
            self.assertEqual(user_refs, "")
            cache_repo = next(cache.glob("*.git"))
            cached_refs = git(cache_repo, "for-each-ref", "--format=%(refname)", "refs/htail/branches")
            self.assertIn("refs/htail/branches/", cached_refs)

    def test_source_switch_passes_picker_sha_to_follower(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = GitRepoFixture(Path(td))
            args = app.parse_args([str(fixture.file), "--no-native-watch", "--no-color"])
            application = app.MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))
            try:
                application._open_git_source_palette()
                deadline = __import__("time").monotonic() + 3.0
                while application._git_source_refs_thread is not None and __import__("time").monotonic() < deadline:
                    application.tick(__import__("time").monotonic())
                    __import__("time").sleep(0.01)
                selected = next(ref for ref in application._git_source_refs if ref.remote == "origin" and ref.branch == "main")
                self.assertIsNotNone(selected.sha)
                captured = {}
                original_init = GitRemoteFollower.__init__

                def capture_init(self, context, remote, branch, follower_args, **kwargs):
                    captured["initial_sha"] = kwargs.get("initial_sha")
                    original_init(self, context, remote, branch, follower_args, **kwargs)

                with mock.patch.object(GitRemoteFollower, "__init__", capture_init):
                    application._switch_active_git_source_worker(("origin", "main"))
                self.assertEqual(captured["initial_sha"], selected.sha)
            finally:
                application.close_native_watch()


class PaneResizeTests(unittest.TestCase):
    def test_weighted_columns_cover_the_same_total_width(self):
        rects = pane_rects("columns", 3, 100, 20, [2.0, 1.0, 1.0])
        self.assertEqual(sum(rect.width for rect in rects), 100)
        self.assertGreater(rects[0].width, rects[1].width)
        self.assertEqual({rect.height for rect in rects}, {20})

    def test_ctrl_arrows_resize_and_equalize_columns(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "a.txt"
            second = root / "b.txt"
            first.write_text("a\n", encoding="utf-8")
            second.write_text("b\n", encoding="utf-8")
            args = app.parse_args([str(first), str(second), "--layout", "columns", "--no-native-watch", "--no-color"])
            application = app.MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))
            try:
                application.dimensions = lambda: (120, 30)
                width, height, _ = application.content_dimensions()
                application._pane_boxes(width, height)
                before = application.last_rects[0][1].width
                application.handle_input("CTRL_RIGHT")
                application._pane_boxes(width, height)
                after = application.last_rects[0][1].width
                self.assertGreater(after, before)
                application._equalize_pane_sizes()
                application._pane_boxes(width, height)
                widths = [rect.width for _, rect in application.last_rects]
                self.assertLessEqual(abs(widths[0] - widths[1]), 1)
            finally:
                application.close_native_watch()

    def test_ctrl_arrows_resize_rows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "a.txt"
            second = root / "b.txt"
            first.write_text("a\n", encoding="utf-8")
            second.write_text("b\n", encoding="utf-8")
            args = app.parse_args([str(first), str(second), "--layout", "rows", "--no-native-watch", "--no-color"])
            application = app.MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))
            try:
                application.dimensions = lambda: (120, 30)
                width, height, _ = application.content_dimensions()
                application._pane_boxes(width, height)
                before = application.last_rects[0][1].height
                application.handle_input("CTRL_DOWN")
                application._pane_boxes(width, height)
                after = application.last_rects[0][1].height
                self.assertGreater(after, before)
            finally:
                application.close_native_watch()


if __name__ == "__main__":
    unittest.main()
