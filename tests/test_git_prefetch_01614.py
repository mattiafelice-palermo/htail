from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from htail_app.git_remote import discover_git_file, read_remote_snapshot
from htail_app.git_source_prefetch import list_remote_file_refs, schedule_file_source_prefetch, recommended_source


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


@unittest.skipUnless(shutil.which("git"), "git is required")
class GitPrefetch01614Tests(unittest.TestCase):
    def test_filters_missing_branches_recommends_local_branch_and_warms_blob(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            remote = root / "remote.git"
            work = root / "work"
            cache = root / "cache"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.DEVNULL)
            git(remote, "config", "uploadpack.allowFilter", "true")
            subprocess.run(["git", "init", str(work)], check=True, stdout=subprocess.DEVNULL)
            git(work, "config", "user.email", "htail-test@example.invalid")
            git(work, "config", "user.name", "htail test")
            git(work, "remote", "add", "origin", str(remote))

            watched = work / "docs" / "status.md"
            watched.parent.mkdir(parents=True)
            watched.write_text("main\n", encoding="utf-8")
            (work / "unrelated.bin").write_bytes(b"x" * 1_000_000)
            git(work, "add", ".")
            git(work, "commit", "-m", "main")
            git(work, "branch", "-M", "main")
            git(work, "push", "-u", "origin", "main")

            git(work, "checkout", "-b", "feature/local")
            watched.write_text("recommended\n", encoding="utf-8")
            git(work, "add", "docs/status.md")
            git(work, "commit", "-m", "recommended")
            git(work, "push", "-u", "origin", "feature/local")

            git(work, "checkout", "main")
            git(work, "checkout", "-b", "without-file")
            git(work, "rm", "docs/status.md")
            git(work, "commit", "-m", "remove watched file")
            git(work, "push", "-u", "origin", "without-file")
            git(work, "checkout", "feature/local")

            old_cache = __import__("os").environ.get("HTAIL_GIT_CACHE_DIR")
            __import__("os").environ["HTAIL_GIT_CACHE_DIR"] = str(cache)
            try:
                schedule_file_source_prefetch(watched, "utf-8")
                context = discover_git_file(watched)
                self.assertIsNotNone(context)
                refs, warning = list_remote_file_refs(context)
                self.assertFalse(warning, warning)
                labels = [ref.label for ref in refs]
                self.assertIn("origin/main", labels)
                self.assertIn("origin/feature/local", labels)
                self.assertNotIn("origin/without-file", labels)
                self.assertEqual(refs[0].label, "origin/feature/local")
                self.assertEqual(recommended_source(context), ("origin", "feature/local"))

                # If warming really fetched the recommended file blob, it can
                # still be read after the source remote disappears.
                offline = root / "remote-offline.git"
                remote.rename(offline)
                sha, lines = read_remote_snapshot(
                    context,
                    refs[0].remote,
                    refs[0].branch,
                    "utf-8",
                    expected_sha=refs[0].sha,
                )
                self.assertEqual(sha, refs[0].sha)
                self.assertEqual(lines, ["recommended\n"])
            finally:
                if old_cache is None:
                    __import__("os").environ.pop("HTAIL_GIT_CACHE_DIR", None)
                else:
                    __import__("os").environ["HTAIL_GIT_CACHE_DIR"] = old_cache


if __name__ == "__main__":
    unittest.main()
