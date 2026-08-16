from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from htail_app import core


class RuntimeUpdateTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, content: bytes):
            self._stream = io.BytesIO(content)
            self.headers = {"Content-Length": str(len(content))}

        def read(self, size: int = -1):
            return self._stream.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    @staticmethod
    def make_runtime(runtime_id: str, abi: str) -> bytes:
        wheel_buffer = io.BytesIO()
        with zipfile.ZipFile(wheel_buffer, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
            wheel.writestr("fake_native/__init__.py", "VALUE = 42\n")

        runtime_buffer = io.BytesIO()
        wheel_name = f"fake_native-1.0-{abi}-{abi}-manylinux_x86_64.whl"
        manifest = {
            "format": 1,
            "runtime_id": runtime_id,
            "abi": abi,
            "platform": "manylinux_2_28_x86_64",
            "wheels": [wheel_name],
        }
        with zipfile.ZipFile(runtime_buffer, "w") as runtime:
            runtime.writestr("runtime.json", json.dumps(manifest))
            runtime.writestr("wheels/" + wheel_name, wheel_buffer.getvalue())
        return runtime_buffer.getvalue()

    def test_update_downloads_verifies_and_unpacks_only_selected_runtime_before_install(self):
        runtime_id = "a" * 64
        abi = "cp313"
        new_source = (
            "#!/usr/bin/env python3\n"
            'HTAIL_VERSION = "9.9.9"\n'
            f'HTAIL_RUNTIME_ID = "{runtime_id}"\n'
            "import sys\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print('htail 9.9.9')\n"
            "elif sys.argv[1:] == ['--bundle-self-test']:\n"
            "    print('htail bundle self-test: app 9.9.9')\n"
            "elif sys.argv[1:] == ['--prepare-core']:\n"
            "    pass\n"
            "else:\n"
            "    print('new')\n"
        ).encode()
        runtime_content = self.make_runtime(runtime_id, abi)
        core_digest = hashlib.sha256(new_source).hexdigest().encode()
        runtime_digest = hashlib.sha256(runtime_content).hexdigest().encode()

        release = core.ReleaseInfo(
            version="9.9.9",
            tag="v9.9.9",
            asset_url="https://example.invalid/htail",
            asset_name="htail",
            checksum_url="https://example.invalid/htail.sha256",
            runtime_url=f"https://example.invalid/htail-runtime-{abi}.zip",
            runtime_checksum_url=f"https://example.invalid/htail-runtime-{abi}.zip.sha256",
            runtime_abi=abi,
        )

        payloads = {
            release.asset_url: new_source,
            release.checksum_url: core_digest + b"  htail\n",
            release.runtime_url: runtime_content,
            release.runtime_checksum_url: runtime_digest + f"  htail-runtime-{abi}.zip\n".encode(),
        }

        def fake_urlopen(request, timeout=None):
            return self.FakeResponse(payloads[request.full_url])

        stages = []
        service = core.UpdateService("example/repo", asset_name="htail")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "ht"
            target.write_text('#!/usr/bin/env python3\nHTAIL_VERSION = "0.16.0"\n', encoding="utf-8")
            target.chmod(0o755)
            cache = root / "cache"
            with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache)}, clear=False):
                with mock.patch.object(core.urllib.request, "urlopen", side_effect=fake_urlopen):
                    ok, message = service.install(
                        release,
                        target,
                        progress=lambda stage, current, total: stages.append((stage, current, total)),
                    )

            self.assertTrue(ok, message)
            self.assertEqual(target.read_bytes(), new_source)
            runtime_target = cache / "htail" / "runtime" / runtime_id / abi
            self.assertEqual((runtime_target / "fake_native" / "__init__.py").read_text(), "VALUE = 42\n")

        labels = [stage for stage, _, _ in stages]
        self.assertTrue(any(stage.startswith("Downloading runtime cp313") for stage in labels))
        self.assertTrue(any(stage.startswith("Verifying runtime cp313") for stage in labels))
        self.assertIn("Unpacking application…", labels)
        self.assertIn("Verifying installed application…", labels)
        unpack = [(current, total) for stage, current, total in stages if stage.startswith("Unpacking runtime cp313")]
        self.assertTrue(unpack)
        self.assertEqual(unpack[-1][0], unpack[-1][1])
        self.assertLess(labels.index(next(stage for stage in labels if stage.startswith("Unpacking runtime"))), labels.index("Installing update…"))
        self.assertLess(labels.index("Installing update…"), labels.index("Verifying installed application…"))

    def test_failed_installed_bundle_self_test_restores_backup(self):
        new_source = (
            "#!/usr/bin/env python3\n"
            'HTAIL_VERSION = "9.9.9"\n'
            "import sys\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print('htail 9.9.9')\n"
            "elif sys.argv[1:] == ['--bundle-self-test']:\n"
            "    print('broken bundle', file=sys.stderr)\n"
            "    raise SystemExit(7)\n"
            "elif sys.argv[1:] == ['--prepare-core']:\n"
            "    pass\n"
        ).encode()
        digest = hashlib.sha256(new_source).hexdigest().encode()
        release = core.ReleaseInfo(
            version="9.9.9",
            tag="v9.9.9",
            asset_url="https://example.invalid/htail",
            asset_name="htail",
            checksum_url="https://example.invalid/htail.sha256",
        )
        payloads = {
            release.asset_url: new_source,
            release.checksum_url: digest + b"  htail\n",
        }

        def fake_urlopen(request, timeout=None):
            return self.FakeResponse(payloads[request.full_url])

        service = core.UpdateService("example/repo", asset_name="htail")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "ht"
            old_source = b'#!/usr/bin/env python3\nprint("old")\n'
            target.write_bytes(old_source)
            target.chmod(0o755)
            with mock.patch.object(core.urllib.request, "urlopen", side_effect=fake_urlopen):
                ok, message = service.install(release, target)

            self.assertFalse(ok)
            self.assertIn("restored backup", message)
            self.assertIn("broken bundle", message)
            self.assertEqual(target.read_bytes(), old_source)


if __name__ == "__main__":
    unittest.main()
