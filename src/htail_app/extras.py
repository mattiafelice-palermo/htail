from __future__ import annotations

import bz2
from dataclasses import dataclass
import gzip
import lzma
from pathlib import Path
import re
import shlex
from typing import List, Sequence, Tuple
from urllib.parse import unquote, urlsplit

from . import core


@dataclass(frozen=True)
class OutlineEntry:
    level: int
    source_index: int
    text: str


_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*$", re.I)
_URL_RE = re.compile(r"https?://[^\s<>\]\[(){}\"']+")
COMPRESSED_SUFFIXES = {".gz", ".bz2", ".xz", ".lzma"}


def parse_duration(value: str) -> float:
    """Parse a compact duration such as 30s, 5m or 1.5h into seconds."""
    lowered = value.strip().lower()
    if lowered in {"off", "none", "0"}:
        return 0.0
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError("expected a duration such as 30s, 5m, 1h or off")
    number = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    factor = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    return number * factor


def markdown_outline(lines: Sequence[str]) -> List[OutlineEntry]:
    """Extract ATX Markdown headings while ignoring fenced-code content."""
    result: List[OutlineEntry] = []
    fence = None
    fence_re = re.compile(r"^\s*(```+|~~~+)")
    heading_re = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        fence_match = fence_re.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        match = heading_re.match(line)
        if not match:
            continue
        text = re.sub(r"\s+#+\s*$", "", match.group(2)).strip()
        text = re.sub(r"[*_`~]", "", text).strip()
        result.append(OutlineEntry(len(match.group(1)), index, text))
    return result


def is_compressed_path(path: Path) -> bool:
    return path.suffix.lower() in COMPRESSED_SUFFIXES


def syntax_path_for_source(path: Path) -> Path:
    return Path(path.stem) if is_compressed_path(path) else path


def read_compressed_lines(path: Path, encoding: str) -> List[str]:
    suffix = path.suffix.lower()
    opener = {
        ".gz": gzip.open,
        ".bz2": bz2.open,
        ".xz": lzma.open,
        ".lzma": lzma.open,
    }.get(suffix)
    if opener is None:
        raise ValueError(f"unsupported compressed file: {path}")
    with opener(path, "rt", encoding=encoding, errors="replace") as handle:
        return handle.readlines()


def parse_ssh_source(source: str) -> Tuple[List[str], str]:
    """Return OpenSSH argv and a compact display label for one remote tail."""
    source = source.strip()
    port = None
    if source.startswith("ssh://"):
        parsed = urlsplit(source)
        if not parsed.hostname:
            raise ValueError("SSH URL is missing a host")
        target = parsed.hostname
        if parsed.username:
            target = f"{unquote(parsed.username)}@{target}"
        port = parsed.port
        remote_path = unquote(parsed.path or "")
    else:
        if ":" not in source:
            raise ValueError("SSH source must be ssh://host/path or user@host:/path")
        target, remote_path = source.split(":", 1)
        if not target or not remote_path:
            raise ValueError("SSH source must include both host and remote path")
    if not remote_path.startswith("/"):
        remote_path = "/" + remote_path
    remote_command = "tail -F -- " + shlex.quote(remote_path)
    argv = ["ssh", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
    if port is not None:
        argv.extend(["-p", str(port)])
    argv.extend([target, remote_command])
    return argv, f"ssh:{target}:{remote_path}"


def _visible_boundaries(text: str, plain: str) -> List[int]:
    boundaries = [0] * (len(plain) + 1)
    raw = visible = 0
    while raw < len(text) and visible < len(plain):
        match = core.ANSI_RE.match(text, raw)
        if match:
            raw = match.end()
            continue
        boundaries[visible] = raw
        visible += 1
        raw += 1
    boundaries[visible] = raw
    return boundaries


def linkify_urls(text: str, enabled: bool = True) -> str:
    """Wrap visible HTTP(S) URLs in OSC-8 terminal hyperlinks."""
    if not enabled:
        return text
    plain = core.strip_ansi(text)
    matches = list(_URL_RE.finditer(plain))
    if not matches:
        return text
    boundaries = _visible_boundaries(text, plain)
    for match in reversed(matches):
        start, end = match.span()
        url = match.group(0).rstrip(".,;:")
        end = start + len(url)
        raw_start, raw_end = boundaries[start], boundaries[end]
        open_link = f"\x1b]8;;{url}\x1b\\"
        close_link = "\x1b]8;;\x1b\\"
        text = text[:raw_end] + close_link + text[raw_end:]
        text = text[:raw_start] + open_link + text[raw_start:]
    return text
