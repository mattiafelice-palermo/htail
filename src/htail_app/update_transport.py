"""Network hardening for the frozen-core self-update service."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from . import core


_ORIGINAL_CHECK_LATEST = core.UpdateService.check_latest
_ORIGINAL_INSTALL = core.UpdateService.install
_INSTALL_LOCK = threading.Lock()


def _open_with_retry(opener, request, *, timeout: float, attempts: int = 3):
    """Open a request with bounded retries for transient transport failures."""
    for attempt in range(max(1, attempts)):
        try:
            return opener(request, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.20 * (attempt + 1))
    raise AssertionError("unreachable")


def _asset_digest(asset: object) -> Optional[str]:
    if not isinstance(asset, dict):
        return None
    digest = str(asset.get("digest") or "").strip()
    match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest)
    return match.group(1).lower() if match else None


def _local_checksum_url(digest: str, filename: str) -> str:
    payload = urllib.parse.quote(f"{digest}  {filename}\n", safe="")
    return f"data:text/plain;charset=utf-8,{payload}"


def _check_latest(self: core.UpdateService) -> Optional[core.ReleaseInfo]:
    """Check GitHub once and prefer API asset digests over checksum downloads."""
    if not self.enabled:
        return None

    api_url = f"https://api.github.com/repos/{self.repo}/releases/latest"
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"htail/{core.HTAIL_VERSION}",
        },
    )
    try:
        with _open_with_retry(urllib.request.urlopen, request, timeout=5.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"update check failed: GitHub returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"update check failed: {reason}") from exc

    tag = str(payload.get("tag_name") or "").strip()
    version = tag.lstrip("vV")
    notes = str(payload.get("body") or "")
    if not tag or not core.is_newer_version(version, core.HTAIL_VERSION):
        return None

    assets = payload.get("assets") or []
    asset_url: Optional[str] = None
    checksum_url: Optional[str] = None
    asset_sha256: Optional[str] = None
    runtime_abi = core.current_cpython_abi()
    runtime_name = f"htail-runtime-{runtime_abi}.zip"
    runtime_url: Optional[str] = None
    runtime_checksum_url: Optional[str] = None
    runtime_sha256: Optional[str] = None

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name == self.asset_name and url:
            asset_url = url
            asset_sha256 = _asset_digest(asset)
        elif name in (f"{self.asset_name}.sha256", f"{self.asset_name}.sha256sum") and url:
            checksum_url = url
        elif name == runtime_name and url:
            runtime_url = url
            runtime_sha256 = _asset_digest(asset)
        elif name in (f"{runtime_name}.sha256", f"{runtime_name}.sha256sum") and url:
            runtime_checksum_url = url

    if not asset_url:
        raise RuntimeError(f"release {tag} has no '{self.asset_name}' asset")
    if asset_sha256:
        checksum_url = _local_checksum_url(asset_sha256, self.asset_name)
    if not checksum_url:
        raise RuntimeError(f"release {tag} has no '{self.asset_name}.sha256' checksum asset")
    if runtime_sha256:
        runtime_checksum_url = _local_checksum_url(runtime_sha256, runtime_name)

    return core.ReleaseInfo(
        version=version,
        tag=tag,
        asset_url=asset_url,
        asset_name=self.asset_name,
        checksum_url=checksum_url,
        notes=notes,
        runtime_url=runtime_url,
        runtime_checksum_url=runtime_checksum_url,
        runtime_abi=runtime_abi,
    )


def _install(self: core.UpdateService, release: core.ReleaseInfo, target, progress=None):
    """Run the frozen installer with retrying URL opens for transient failures."""
    with _INSTALL_LOCK:
        opener = core.urllib.request.urlopen

        def retrying_urlopen(request, timeout=None):
            return _open_with_retry(opener, request, timeout=float(timeout or 10.0))

        core.urllib.request.urlopen = retrying_urlopen
        try:
            return _ORIGINAL_INSTALL(self, release, target, progress=progress)
        finally:
            core.urllib.request.urlopen = opener


def install() -> None:
    if getattr(core.UpdateService, "_htail_transport_extension", False):
        return
    core.UpdateService.check_latest = _check_latest
    core.UpdateService.install = _install
    core.UpdateService._htail_transport_extension = True
