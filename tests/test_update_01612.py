from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.parse

from htail_app import core


class FakeResponse:
    def __init__(self, content: bytes):
        self._content = content
        self.headers = {"Content-Length": str(len(content))}

    def read(self, _size=None):
        content, self._content = self._content, b""
        return content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class Update01612Tests(unittest.TestCase):
    @staticmethod
    def new_source() -> bytes:
        return (
            "#!/usr/bin/env python3\n"
            'HTAIL_VERSION = "99.0.0"\n'
            "import sys\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print('htail 99.0.0')\n"
            "elif sys.argv[1:] == ['--bundle-self-test']:\n"
            "    print('htail bundle self-test: app 99.0.0')\n"
            "elif sys.argv[1:] == ['--prepare-core']:\n"
            "    pass\n"
        ).encode()

    def test_check_latest_converts_github_digests_to_local_checksum_urls(self):
        service = core.UpdateService("example/repo")
        abi = core.current_cpython_abi()
        app_digest = "a" * 64
        runtime_digest = "b" * 64
        payload = {
            "tag_name": "v99.0.0",
            "body": "notes",
            "assets": [
                {
                    "name": "htail",
                    "browser_download_url": "https://example.invalid/htail",
                    "digest": f"sha256:{app_digest}",
                },
                {
                    "name": "htail.sha256",
                    "browser_download_url": "https://example.invalid/htail.sha256",
                },
                {
                    "name": f"htail-runtime-{abi}.zip",
                    "browser_download_url": f"https://example.invalid/runtime-{abi}.zip",
                    "digest": f"sha256:{runtime_digest}",
                },
                {
                    "name": f"htail-runtime-{abi}.zip.sha256",
                    "browser_download_url": "https://example.invalid/runtime.sha256",
                },
            ],
        }
        with mock.patch.object(
            core.urllib.request,
            "urlopen",
            return_value=FakeResponse(json.dumps(payload).encode()),
        ):
            release = service.check_latest()

        self.assertIsNotNone(release)
        assert release is not None
        self.assertTrue(release.checksum_url.startswith("data:text/plain"))
        self.assertTrue(release.runtime_checksum_url.startswith("data:text/plain"))
        self.assertIn(app_digest, urllib.parse.unquote(release.checksum_url))
        self.assertIn(runtime_digest, urllib.parse.unquote(release.runtime_checksum_url))

    def test_install_with_api_digest_uses_no_https_checksum_request(self):
        new_source = self.new_source()
        digest = hashlib.sha256(new_source).hexdigest()
        checksum_url = "data:text/plain," + urllib.parse.quote(f"{digest}  htail\n", safe="")
        release = core.ReleaseInfo(
            version="99.0.0",
            tag="v99.0.0",
            asset_url="https://example.invalid/htail",
            asset_name="htail",
            checksum_url=checksum_url,
        )
        requested = []

        real_urlopen = core.urllib.request.urlopen

        def fake_urlopen(request, timeout=None):
            requested.append(request.full_url)
            if request.full_url == release.asset_url:
                return FakeResponse(new_source)
            return real_urlopen(request, timeout=timeout)

        service = core.UpdateService("example/repo")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "ht"
            target.write_text('#!/usr/bin/env python3\nHTAIL_VERSION = "0.0.0"\n', encoding="utf-8")
            target.chmod(0o755)
            with mock.patch.object(core.urllib.request, "urlopen", side_effect=fake_urlopen):
                ok, message = service.install(release, target)

        self.assertTrue(ok, message)
        self.assertEqual([url for url in requested if url.startswith("https://")], [release.asset_url])
        self.assertIn("SHA-256 verified", message)

    def test_install_retries_transient_checksum_handshake_timeout(self):
        new_source = self.new_source()
        digest = hashlib.sha256(new_source).hexdigest()
        release = core.ReleaseInfo(
            version="99.0.0",
            tag="v99.0.0",
            asset_url="https://example.invalid/htail",
            asset_name="htail",
            checksum_url="https://example.invalid/htail.sha256",
        )
        checksum_attempts = 0

        def flaky_urlopen(request, timeout=None):
            nonlocal checksum_attempts
            if request.full_url == release.asset_url:
                return FakeResponse(new_source)
            if request.full_url == release.checksum_url:
                checksum_attempts += 1
                if checksum_attempts == 1:
                    raise urllib.error.URLError(TimeoutError("The handshake operation timed out"))
                return FakeResponse((digest + "  htail\n").encode())
            raise AssertionError(request.full_url)

        service = core.UpdateService("example/repo")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "ht"
            target.write_text('#!/usr/bin/env python3\nHTAIL_VERSION = "0.0.0"\n', encoding="utf-8")
            target.chmod(0o755)
            with mock.patch("htail_app.update_transport.time.sleep", return_value=None), mock.patch.object(
                core.urllib.request,
                "urlopen",
                side_effect=flaky_urlopen,
            ):
                ok, message = service.install(release, target)

        self.assertTrue(ok, message)
        self.assertEqual(checksum_attempts, 2)


if __name__ == "__main__":
    unittest.main()
