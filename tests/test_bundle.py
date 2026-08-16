from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from htail_app import VERSION


ROOT = Path(__file__).resolve().parents[1]


class BundleTests(unittest.TestCase):
    def test_generated_wrapper_reports_version(self):
        # Unit tests validate wrapper generation without downloading every
        # native ABI payload. CI separately builds the full vendor bundle and
        # runs --bundle-self-test against the extracted native environment.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "htail"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "build_release.py"),
                    "--output",
                    str(out),
                ],
                check=True,
            )
            result = subprocess.run([sys.executable, str(out), "--version"], check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), f"htail {VERSION}")
            text = out.read_text(encoding="utf-8")
            self.assertIn(f'HTAIL_VERSION = "{VERSION}"', text)


if __name__ == "__main__":
    unittest.main()
