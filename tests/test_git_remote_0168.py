from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from htail_app import app, core
from htail_app.git_remote import GitRemoteFollower, discover_git_file, list_remote_refs
from htail_app.watcher import FileFollower, WatchUpdate


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


class GitRepoFixture:
    def __init__(self, root: Path) -> None:
        self.remote = root / "remote.git"
        self.work = root / "work"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "init", str(self.work)], check=True, stdout=subprocess.DEVNULL)
        git(self.work, "config", "user.email", "htail-test@example.invalid")
        git(self.work, "config", "user.name", "htail test")
        git(self.work, "remote", "add", "origin", str(self.remote))
        self.file = self.work / "docs" / "status.md"
        self.file.parent.mkdir(parents=True)
        self.file.write_text("remote one\n", encoding="utf-8")
        git(self.work, "add", "docs/status.md")
        git(self.work, "commit", "-m", "initial")
        git(self.work, "branch", "-M", "main")
        git(self.work, "push", "-u", "origin", "main")

    def push_append(self, text: str) -> None:
        with self.file.open("a", encoding="utf-8") as handle:
            handle.write(text)
        git(self.work, "add", "docs/status.md")
        git(self.work, "commit", "-m", "remote update")
        git(self.work, "push", "origin", "main")


@unittest.skipUnless(shutil.which("git"), "git is required")
class GitRemoteSourceTests(unittest.TestCase):
    def test_discovers_same_file_identity_and_remote_branch(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = GitRepoFixture(Path(td))
            context = discover_git_file(fixture.file)
            self.assertIsNotNone(context)
            self.assertEqual(context.relative_path, "docs/status.md")
            self.assertEqual(context.remotes, ("origin",))
            refs, warning = list_remote_refs(context)
            self.assertIsNone(warning)
            self.assertIn("origin/main", [ref.label for ref in refs])

    def test_remote_follower_emits_existing_watch_update_shape(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = GitRepoFixture(Path(td))
            context = discover_git_file(fixture.file)
            args = Namespace(encoding="utf-8", lines=None)
            follower = GitRemoteFollower(context, "origin", "main", args, check_interval=1.0)
            notice = follower.initialize_if_available()
            self.assertEqual(notice.initial_tail, ["remote one\n"])

            fixture.push_append("remote two\n")
            self.assertIsNone(follower.poll(time.monotonic() + 2.0))
            deadline = time.monotonic() + 3.0
            update = None
            while update is None and time.monotonic() < deadline:
                time.sleep(0.01)
                update = follower.poll(time.monotonic() + 2.0)
            self.assertIsInstance(update, WatchUpdate)
            self.assertEqual(update.added, 1)
            self.assertEqual(list(update.current_snapshot), ["remote one\n", "remote two\n"])

    def test_command_palette_switches_same_pane_remote_and_back(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = GitRepoFixture(Path(td))
            fixture.file.write_text("local only\n", encoding="utf-8")
            args = app.parse_args([str(fixture.file), "--no-native-watch", "--no-color"])
            application = app.MultiApp(args, False, core.DisplayFilter(), core.UpdateService(""))
            try:
                self.assertIsInstance(application.followers[0], FileFollower)
                self.assertEqual(application.panes[0].snapshot_raw, ["local only\n"])

                application._open_palette()
                switch_index = next(
                    i for i, item in enumerate(application.palette_items)
                    if item.action == "git-source"
                )
                application.palette_selected = switch_index
                application._execute_palette_item()
                self.assertEqual(application.palette_mode, "git-source")
                remote_index = next(
                    i for i, item in enumerate(application.palette_items)
                    if item.value == ("origin", "main")
                )
                application.palette_selected = remote_index
                application._execute_palette_item()

                self.assertIsInstance(application.followers[0], GitRemoteFollower)
                self.assertEqual(application.panes[0].path, fixture.file)
                self.assertEqual(application.panes[0].snapshot_raw, ["remote one\n"])
                self.assertIn("origin/main", core.strip_ansi(application.panes[0].title(0, 120, True)))

                application._open_palette()
                switch_index = next(
                    i for i, item in enumerate(application.palette_items)
                    if item.action == "git-source"
                )
                application.palette_selected = switch_index
                application._execute_palette_item()
                local_index = next(
                    i for i, item in enumerate(application.palette_items)
                    if item.value is None
                )
                application.palette_selected = local_index
                application._execute_palette_item()

                self.assertIsInstance(application.followers[0], FileFollower)
                self.assertEqual(application.panes[0].snapshot_raw, ["local only\n"])
                self.assertNotIn("origin/main", core.strip_ansi(application.panes[0].title(0, 120, True)))
                application._open_palette()
                switch_index = next(
                    i for i, item in enumerate(application.palette_items)
                    if item.action == "git-source"
                )
                application.palette_selected = switch_index
                application._execute_palette_item()
                self.assertTrue(application.palette_items[0].label.startswith("✓ Local working tree"))
            finally:
                application.close_native_watch()


if __name__ == "__main__":
    unittest.main()