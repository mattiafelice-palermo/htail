from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from htail_app import VERSION


ROOT = Path(__file__).resolve().parents[1]


class BundleTests(unittest.TestCase):
    def build_wrapper(self, root: Path) -> Path:
        out = root / "htail"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "build_release.py"),
                "--output",
                str(out),
            ],
            check=True,
        )
        return out

    def test_generated_wrapper_reports_version(self):
        # Unit tests validate wrapper generation without downloading every
        # native ABI payload. CI separately builds the full vendor bundle and
        # runs --bundle-self-test against the extracted native environment.
        with tempfile.TemporaryDirectory() as tmp:
            out = self.build_wrapper(Path(tmp))
            result = subprocess.run([sys.executable, str(out), "--version"], check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), f"htail {VERSION}")
            text = out.read_text(encoding="utf-8")
            self.assertIn(f'HTAIL_VERSION = "{VERSION}"', text)

    def test_launcher_discards_preloaded_stale_htail_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = self.build_wrapper(root)
            wrapper_text = out.read_text(encoding="utf-8")
            match = re.search(r'^HTAIL_RUNTIME_ID = "([^"]+)"', wrapper_text, re.MULTILINE)
            self.assertIsNotNone(match)
            runtime_id = match.group(1)

            cache = root / "cache"
            abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
            # Avoid network bootstrap; --help does not need the native runtime.
            (cache / "htail" / "runtime" / runtime_id / abi).mkdir(parents=True)

            stale = root / "stale"
            package = stale / "htail_app"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text('VERSION = "STALE"\n', encoding="utf-8")
            (package / "app.py").write_text(
                'def main():\n    print("STALE APP SELECTED")\n    return 0\n',
                encoding="utf-8",
            )
            # Python imports sitecustomize before launcher.py. Deliberately
            # preload a stale htail_app from an inherited environment to model
            # the worst case for an in-process self-update restart.
            (stale / "sitecustomize.py").write_text(
                "import os, sys\n"
                "root = os.path.dirname(__file__)\n"
                "sys.path.insert(0, root)\n"
                "import htail_app.app\n",
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["XDG_CACHE_HOME"] = str(cache)
            env["PYTHONPATH"] = str(stale)
            result = subprocess.run(
                [sys.executable, str(out), "--help"],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, combined)
            self.assertNotIn("STALE APP SELECTED", combined)
            self.assertIn("usage:", combined.lower())

    def test_prepare_core_repairs_invalid_existing_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = self.build_wrapper(root)
            cache = root / "cache"
            env = os.environ.copy()
            env["XDG_CACHE_HOME"] = str(cache)

            subprocess.run([sys.executable, str(out), "--prepare-core"], env=env, check=True)
            env_dirs = list((cache / "htail" / VERSION).glob("env-*"))
            self.assertEqual(len(env_dirs), 1)
            app_env = env_dirs[0]
            manifest_path = app_env / "bundle.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], VERSION)

            manifest["version"] = "STALE"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            app_path = app_env / "app" / "htail_app" / "app.py"
            app_path.write_text("STALE_CACHE = True\n", encoding="utf-8")

            subprocess.run([sys.executable, str(out), "--prepare-core"], env=env, check=True)
            repaired = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(repaired["version"], VERSION)
            self.assertNotIn("STALE_CACHE", app_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
