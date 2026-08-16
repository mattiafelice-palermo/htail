from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import io
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from htail_app import app, core
from htail_app.git_remote import discover_git_file, read_remote_snapshot
from htail_app import update_transport


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
class RemotePartialFetchTests(unittest.TestCase):
    def test_remote_snapshot_uses_blobless_cache_for_selected_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            remote = root / "remote.git"
            producer = root / "producer"
            consumer = root / "consumer"
            cache = root / "cache"

            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.DEVNULL)
            git(remote, "config", "uploadpack.allowFilter", "true")
            subprocess.run(["git", "init", str(producer)], check=True, stdout=subprocess.DEVNULL)
            git(producer, "config", "user.email", "htail-test@example.invalid")
            git(producer, "config", "user.name", "htail test")
            git(producer, "remote", "add", "origin", str(remote))
            (producer / "docs").mkdir()
            (producer / "docs" / "status.md").write_text("selected file\n", encoding="utf-8")
            (producer / "assets").mkdir()
            (producer / "assets" / "unrelated.bin").write_bytes(os.urandom(2_000_000))
            git(producer, "add", ".")
            git(producer, "commit", "-m", "fixture")
            git(producer, "branch", "-M", "main")
            git(producer, "push", "origin", "main")
            sha = git(producer, "rev-parse", "HEAD")

            subprocess.run(["git", "init", str(consumer)], check=True, stdout=subprocess.DEVNULL)
            git(consumer, "remote", "add", "origin", str(remote))
            (consumer / "docs").mkdir()
            local_file = consumer / "docs" / "status.md"
            local_file.write_text("local file\n", encoding="utf-8")
            context = discover_git_file(local_file)
            self.assertIsNotNone(context)

            with mock.patch.dict(os.environ, {"HTAIL_GIT_CACHE_DIR": str(cache)}):
                fetched_sha, lines = read_remote_snapshot(
                    context, "origin", "main", "utf-8", expected_sha=sha
                )

            self.assertEqual(fetched_sha, sha)
            self.assertEqual(lines, ["selected file\n"])
            pack_bytes = sum(path.stat().st_size for path in cache.rglob("*.pack"))
            self.assertLess(pack_bytes, 250_000)


class UpdateCompletionTests(unittest.TestCase):
    def test_success_is_rendered_at_100_percent_before_install_returns(self):
        owner = SimpleNamespace(
            update_install_status="",
            update_install_progress=(1, 2),
            update_overall_progress=0.42,
            dirty=False,
            render_frames=7,
        )

        def make_progress(self):
            def progress(stage, current, total):
                self.update_install_status = stage
            return progress

        progress = make_progress(owner)

        def render_next_frame():
            deadline = time.monotonic() + 0.2
            while time.monotonic() < deadline and owner.update_overall_progress < 1.0:
                time.sleep(0.002)
            owner.render_frames += 1

        import threading
        worker = threading.Thread(target=render_next_frame)
        worker.start()
        rendered = update_transport._show_completion_before_return(progress, timeout=0.2)
        worker.join()
        self.assertTrue(rendered)
        self.assertEqual(owner.update_overall_progress, 1.0)
        self.assertEqual(owner.update_install_status, "Update complete — restarting…")
        self.assertIsNone(owner.update_install_progress)

    def test_transport_labels_real_network_connections(self):
        release = SimpleNamespace(
            asset_url="https://example.invalid/htail",
            runtime_url="https://example.invalid/runtime.zip",
            checksum_url="https://example.invalid/htail.sha256",
            runtime_checksum_url="https://example.invalid/runtime.zip.sha256",
        )
        self.assertEqual(
            update_transport._connection_stage(release, release.asset_url),
            "Downloading release · connecting…",
        )
        self.assertEqual(
            update_transport._connection_stage(release, release.runtime_url),
            "Downloading runtime · connecting…",
        )
        self.assertEqual(
            update_transport._connection_stage(release, release.checksum_url),
            "Verifying release SHA-256 checksum · connecting…",
        )
        release.checksum_url = "data:text/plain,abc"
        self.assertIsNone(update_transport._connection_stage(release, release.checksum_url))


if __name__ == "__main__":
    unittest.main()
