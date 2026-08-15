from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BundleTests(unittest.TestCase):
    def test_generated_wrapper_reports_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "htail"
            subprocess.run([sys.executable, str(ROOT / "tools" / "build_release.py"), "--output", str(out)], check=True)
            result = subprocess.run([sys.executable, str(out), "--version"], check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), "htail 0.8.3")
            text = out.read_text(encoding="utf-8")
            self.assertIn('HTAIL_VERSION = "0.8.3"', text)


if __name__ == "__main__":
    unittest.main()
