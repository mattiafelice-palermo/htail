import hashlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "htail"


def load_htail():
    loader = importlib.machinery.SourceFileLoader("htail_test_module", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    loader.exec_module(module)
    return module


htail = load_htail()


class WrapTests(unittest.TestCase):
    def visible_rows(self, text, width):
        return [htail.strip_ansi(row) for row in htail.wrap_ansi(text, width)]

    def test_plain_soft_wrap_preserves_all_text(self):
        text = "A long line with enough words to wrap cleanly across several terminal rows."
        rows = self.visible_rows(text, 24)
        self.assertGreater(len(rows), 1)
        rebuilt = " ".join(row.strip() for row in rows)
        self.assertEqual(rebuilt, text)
        self.assertTrue(all(len(row) <= 24 for row in rows))

    def test_markdown_bullet_uses_hanging_indent(self):
        rows = self.visible_rows(
            "• This bullet is deliberately long enough to wrap onto several visual rows.",
            32,
        )
        self.assertGreater(len(rows), 1)
        self.assertTrue(rows[0].startswith("• "))
        self.assertTrue(all(row.startswith("  ") for row in rows[1:]))

    def test_changed_markdown_bullet_aligns_under_text(self):
        rows = self.visible_rows(
            "▌ • This changed bullet is deliberately long enough to wrap onto several rows.",
            34,
        )
        self.assertGreater(len(rows), 1)
        self.assertTrue(rows[0].startswith("▌ • "))
        self.assertTrue(all(row.startswith("    ") for row in rows[1:]))

    def test_numbered_list_preserves_nested_indent(self):
        rows = self.visible_rows(
            "  12. This numbered item is deliberately long enough to wrap several times.",
            34,
        )
        self.assertGreater(len(rows), 1)
        self.assertTrue(all(row.startswith("      ") for row in rows[1:]))


class DiffTests(unittest.TestCase):
    def test_initial_line_limit_never_caps_follow_updates(self):
        old = [f"old {i}\n" for i in range(10)]
        appended = [f"new {i}\n" for i in range(75)]
        events, added, replaced, deleted = htail.compute_changes(old, old + appended)
        self.assertEqual(added, 75)
        self.assertEqual(replaced, 0)
        self.assertEqual(deleted, 0)
        self.assertEqual(events, [("add", appended)])

    def test_newline_style_difference_does_not_hide_append(self):
        old = ["one\n", "two\n"]
        new = ["one\r\n", "two\r\n", "three\r\n"]
        events, added, _, _ = htail.compute_changes(old, new)
        visible = [line.strip() for kind, lines in events if kind != "delete" for line in lines]
        self.assertIn("three", visible)
        self.assertGreaterEqual(added, 1)


class VersionTests(unittest.TestCase):
    def test_version_comparison(self):
        self.assertTrue(htail.is_newer_version("0.7.1", "0.7.0"))
        self.assertTrue(htail.is_newer_version("v1.0.0", "0.9.9"))
        self.assertFalse(htail.is_newer_version("0.7.0", "0.7.0"))
        self.assertFalse(htail.is_newer_version("0.6.9", "0.7.0"))


class UpdateTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, content: bytes):
            self._content = content

        def read(self):
            return self._content

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def test_atomic_update_validates_checksum_and_keeps_backup(self):
        service = htail.UpdateService("example/repo", asset_name="htail")
        new_source = b'#!/usr/bin/env python3\nHTAIL_VERSION = "0.7.1"\nprint("new")\n'
        digest = hashlib.sha256(new_source).hexdigest().encode()
        release = htail.ReleaseInfo(
            version="0.7.1",
            tag="v0.7.1",
            asset_url="https://example.invalid/htail",
            asset_name="htail",
            checksum_url="https://example.invalid/htail.sha256",
        )

        def fake_urlopen(request, timeout=None):
            url = request.full_url
            if url.endswith(".sha256"):
                return self.FakeResponse(digest + b"  htail\n")
            return self.FakeResponse(new_source)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "ht"
            target.write_text('#!/usr/bin/env python3\nHTAIL_VERSION = "0.7.0"\n', encoding="utf-8")
            target.chmod(0o755)
            with mock.patch.object(htail.urllib.request, "urlopen", side_effect=fake_urlopen):
                ok, message = service.install(release, target)

            self.assertTrue(ok, message)
            self.assertEqual(target.read_bytes(), new_source)
            self.assertTrue((Path(tmpdir) / "ht.bak").exists())
            self.assertIn("SHA-256 verified", message)


if __name__ == "__main__":
    unittest.main()
