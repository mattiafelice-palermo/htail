from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from htail_app import app, core
from htail_app.git_remote import GitRemoteRef
from htail_app.git_source_prefetch import _decorate_recommended_items
from htail_app.watcher import FileFollower


class SourcePicker01614Tests(unittest.TestCase):
    def test_recommended_remote_branch_gets_star_marker(self):
        items = [
            app.PaletteItem("Local working tree", "git-source-select", None, "LOCAL"),
            app.PaletteItem("feature/work", "git-source-select", ("origin", "feature/work"), "origin"),
            app.PaletteItem("main", "git-source-select", ("origin", "main"), "origin"),
        ]
        refs = [
            GitRemoteRef("origin", "feature/work", "a" * 40),
            GitRemoteRef("origin", "main", "b" * 40),
        ]
        decorated = _decorate_recommended_items(items, ("origin", "feature/work"), app.PaletteItem)
        self.assertEqual(decorated[1].label, "★ feature/work")
        self.assertEqual(decorated[2].label, "main")

    def test_file_open_schedules_background_git_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "note.md"
            path.write_text("hello\n", encoding="utf-8")
            args = Namespace(encoding="utf-8", lines=None)
            follower = FileFollower(path, args)
            with mock.patch("htail_app.git_source_prefetch.schedule_file_source_prefetch") as schedule:
                notice = follower.initialize_if_available()
            self.assertIsNotNone(notice)
            schedule.assert_called_once_with(path, "utf-8")

    def test_release_note_parser_scopes_current_release_and_drops_none(self):
        notes = """# htail 2.0.0

## New features

- None.

## Bug fixes

- Current fix.

# htail 1.9.0

## New features

- Historical feature.

## Bug fixes

- Historical fix.
"""
        features, fixes, other = core.release_note_sections(notes)
        self.assertEqual(features, [])
        self.assertEqual(fixes, ["Current fix."])
        self.assertEqual(other, [])


if __name__ == "__main__":
    unittest.main()
