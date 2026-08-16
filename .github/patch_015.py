from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# New helpers: outline, durations, compressed files, SSH parsing, hyperlinks
# ---------------------------------------------------------------------------
write("src/htail_app/extras.py", r'''from __future__ import annotations

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
''')


# ---------------------------------------------------------------------------
# searching.py: add Boolean mode with AND/OR/NOT and parentheses
# ---------------------------------------------------------------------------
write("src/htail_app/searching.py", r'''from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterator, List, Optional, Pattern, Sequence, Tuple

SEARCH_SIMPLE = "simple"
SEARCH_REGEX = "regex"
SEARCH_BOOLEAN = "boolean"
SEARCH_MODES = (SEARCH_SIMPLE, SEARCH_REGEX, SEARCH_BOOLEAN)


@dataclass(frozen=True)
class GlobalSearchMatch:
    pane_index: int
    source_index: int
    pane_name: str
    text: str
    match_start: int
    match_end: int


def simple_pattern_to_regex(expression: str) -> str:
    """Translate shell-like wildcards while keeping all other text literal."""
    out = []
    escaped = False
    for ch in expression:
        if escaped:
            out.append(re.escape(ch))
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    if escaped:
        out.append(re.escape("\\"))
    return "".join(out)


def simple_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")


class BooleanSearchError(ValueError):
    pass


def _tokenize_boolean(expression: str):
    tokens = []
    i = 0
    while i < len(expression):
        if expression[i].isspace():
            i += 1
            continue
        ch = expression[i]
        if ch in "()":
            tokens.append((ch, ch))
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            buf = []
            while i < len(expression) and expression[i] != quote:
                if expression[i] == "\\" and i + 1 < len(expression):
                    i += 1
                buf.append(expression[i])
                i += 1
            if i >= len(expression):
                raise BooleanSearchError("unterminated quoted phrase")
            i += 1
            tokens.append(("TERM", "".join(buf)))
            continue
        start = i
        while i < len(expression) and not expression[i].isspace() and expression[i] not in "()":
            i += 1
        word = expression[start:i]
        upper = word.upper()
        tokens.append((upper if upper in {"AND", "OR", "NOT"} else "TERM", word))
    return tokens


class _BooleanParser:
    def __init__(self, tokens, flags: int):
        self.tokens = tokens
        self.flags = flags
        self.pos = 0
        self.patterns: List[Pattern[str]] = []

    def peek(self):
        return self.tokens[self.pos][0] if self.pos < len(self.tokens) else None

    def take(self, kind=None):
        if self.pos >= len(self.tokens):
            raise BooleanSearchError("unexpected end of expression")
        token = self.tokens[self.pos]
        if kind is not None and token[0] != kind:
            raise BooleanSearchError(f"expected {kind}, found {token[1]!r}")
        self.pos += 1
        return token

    def parse(self):
        if not self.tokens:
            raise BooleanSearchError("empty Boolean expression")
        node = self.parse_or()
        if self.peek() is not None:
            raise BooleanSearchError(f"unexpected token {self.tokens[self.pos][1]!r}")
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.peek() == "OR":
            self.take("OR")
            node = ("OR", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.peek() in {"AND", "TERM", "NOT", "("}:
            if self.peek() == "AND":
                self.take("AND")
            node = ("AND", node, self.parse_not())
        return node

    def parse_not(self):
        if self.peek() == "NOT":
            self.take("NOT")
            return ("NOT", self.parse_not())
        return self.parse_primary()

    def parse_primary(self):
        if self.peek() == "(":
            self.take("(")
            node = self.parse_or()
            self.take(")")
            return node
        _, value = self.take("TERM")
        try:
            pattern = re.compile(simple_pattern_to_regex(value), self.flags)
        except re.error as exc:
            raise BooleanSearchError(str(exc)) from exc
        index = len(self.patterns)
        self.patterns.append(pattern)
        return ("TERM", index)


def _eval_boolean(node, patterns: Sequence[Pattern[str]], text: str) -> bool:
    op = node[0]
    if op == "TERM":
        return patterns[node[1]].search(text) is not None
    if op == "NOT":
        return not _eval_boolean(node[1], patterns, text)
    if op == "AND":
        return _eval_boolean(node[1], patterns, text) and _eval_boolean(node[2], patterns, text)
    if op == "OR":
        return _eval_boolean(node[1], patterns, text) or _eval_boolean(node[2], patterns, text)
    return False


def _positive_terms(node, negated=False):
    op = node[0]
    if op == "TERM":
        return [] if negated else [node[1]]
    if op == "NOT":
        return _positive_terms(node[1], not negated)
    result = []
    for child in node[1:]:
        result.extend(_positive_terms(child, negated))
    return result


class BooleanPattern:
    """Pattern-like predicate used by Pane without changing its search API."""

    def __init__(self, expression: str, flags: int = 0):
        parser = _BooleanParser(_tokenize_boolean(expression), flags)
        self.root = parser.parse()
        self.patterns = parser.patterns
        self.positive = list(dict.fromkeys(_positive_terms(self.root)))

    def search(self, text: str):
        if not _eval_boolean(self.root, self.patterns, text):
            return None
        matches = [self.patterns[i].search(text) for i in self.positive]
        matches = [match for match in matches if match is not None]
        if matches:
            return min(matches, key=lambda match: match.start())
        return re.search(r".+|^$", text)

    def finditer(self, text: str) -> Iterator[re.Match[str]]:
        if not _eval_boolean(self.root, self.patterns, text):
            return iter(())
        found = []
        seen = set()
        for index in self.positive:
            for match in self.patterns[index].finditer(text):
                key = match.span()
                if key not in seen:
                    seen.add(key)
                    found.append(match)
        if not found:
            fallback = re.search(r".+", text)
            if fallback is not None:
                found.append(fallback)
        found.sort(key=lambda match: (match.start(), match.end()))
        return iter(found)


def compile_search(expression: str, mode: str, flags: int = 0):
    if not expression:
        return None, None
    if mode == SEARCH_BOOLEAN:
        try:
            return BooleanPattern(expression, flags), None
        except BooleanSearchError as exc:
            return None, str(exc)
    if mode == SEARCH_SIMPLE:
        source = simple_pattern_to_regex(expression)
    elif mode == SEARCH_REGEX:
        source = expression
    else:
        return None, f"unknown search mode: {mode}"
    try:
        return re.compile(source, flags), None
    except re.error as exc:
        return None, str(exc)


def search_label(expression: str, mode: str) -> str:
    if mode == SEARCH_REGEX:
        return f"/{expression}/"
    if mode == SEARCH_BOOLEAN:
        return f"bool:{expression}"
    return expression


def preview_around_match(text: str, start: int, end: int, limit: int) -> tuple[str, int, int]:
    text = text.rstrip("\r\n").replace("\t", "    ")
    limit = max(12, limit)
    if len(text) <= limit:
        return text, start, end
    center = (start + end) // 2
    left = max(0, min(center - limit // 2, len(text) - limit))
    right = min(len(text), left + limit)
    preview = text[left:right]
    pstart = max(0, start - left)
    pend = max(pstart, min(len(preview), end - left))
    if left > 0:
        preview = "…" + preview[1:]
    if right < len(text):
        preview = preview[:-1] + "…"
    return preview, pstart, pend
''')


# ---------------------------------------------------------------------------
# sources.py: compressed static sources + SSH + process lifecycle
# ---------------------------------------------------------------------------
write("src/htail_app/sources.py", r'''from __future__ import annotations

import queue
from pathlib import Path
import subprocess
import threading
import time
from typing import List, Optional, TextIO

from .extras import parse_ssh_source, read_compressed_lines
from .watcher import WatchNotice, WatchUpdate


class StreamFollower:
    """Turn a line-oriented text stream into htail update batches."""

    def __init__(self, stream: TextIO, args, label: str = "stdin") -> None:
        self.stream = stream
        self.args = args
        self.label = label
        self.previous: List[str] = []
        self.update_number = 0
        self.last_update_time: Optional[float] = None
        self.initialized = False
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._done = threading.Event()
        self._error: Optional[str] = None
        self._end_reported = False
        self._thread: Optional[threading.Thread] = None

    @property
    def finished(self) -> bool:
        return self._done.is_set() and self._queue.empty() and self._end_reported

    def lifecycle_text(self, now: Optional[float] = None) -> str:
        return ""

    def initialize_if_available(self) -> WatchNotice:
        if not self.initialized:
            self.initialized = True
            self._thread = threading.Thread(target=self._reader, name=f"htail-{self.label}", daemon=True)
            self._thread.start()
        return WatchNotice("initial", initial_tail=[])

    def _reader(self) -> None:
        try:
            while True:
                line = self.stream.readline()
                if line == "":
                    break
                self._queue.put(line)
        except Exception as exc:
            self._error = str(exc)
        finally:
            self._done.set()

    def _end_text(self) -> str:
        return f"{self.label} reached EOF"

    def poll(self, now: Optional[float] = None):
        now = time.monotonic() if now is None else now
        if not self.initialized:
            return self.initialize_if_available()
        fresh: List[str] = []
        while True:
            try:
                fresh.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if fresh:
            start = len(self.previous)
            self.previous.extend(fresh)
            elapsed = None if self.last_update_time is None else now - self.last_update_time
            self.last_update_time = now
            self.update_number += 1
            return WatchUpdate(
                update_number=self.update_number,
                events=(("add", list(fresh)),),
                added=len(fresh), replaced=0, deleted=0, elapsed=elapsed,
                now_monotonic=now, current_snapshot=self.previous,
                changed_new_indices=tuple(range(start, len(self.previous))),
            )
        if self._done.is_set() and not self._end_reported:
            self._end_reported = True
            if self._error:
                return WatchNotice("error", f"{self.label}: {self._error}")
            return WatchNotice("ended", self._end_text())
        return None

    def close(self) -> None:
        return


class CommandFollower(StreamFollower):
    """Run a shell command and expose merged stdout/stderr as a pane source."""

    def __init__(self, command: str, args, label: Optional[str] = None) -> None:
        self.command = command
        self.started_monotonic = time.monotonic()
        self.process = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding=args.encoding, errors="replace", bufsize=1,
        )
        assert self.process.stdout is not None
        super().__init__(self.process.stdout, args, label=label or command)

    def lifecycle_text(self, now: Optional[float] = None) -> str:
        now = time.monotonic() if now is None else now
        runtime = max(0.0, now - self.started_monotonic)
        code = self.process.poll()
        if code is None:
            return f"PID {self.process.pid} · {runtime:.0f}s"
        return f"EXIT {code} · {runtime:.0f}s"

    def _end_text(self) -> str:
        code = self.process.poll()
        return f"command ended: {self.command}" if code is None else f"command exited with status {code}"

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.process.terminate(); self.process.wait(timeout=0.5)
            except Exception:
                try:
                    self.process.kill(); self.process.wait(timeout=0.5)
                except Exception:
                    pass
        if self.process.stdout is not None:
            try:
                self.process.stdout.close()
            except Exception:
                pass


class SSHFollower(StreamFollower):
    """Follow a remote file through the user's existing OpenSSH configuration."""

    def __init__(self, source: str, args) -> None:
        self.source = source
        argv, label = parse_ssh_source(source)
        self.argv = argv
        self.started_monotonic = time.monotonic()
        try:
            self.process = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding=args.encoding, errors="replace", bufsize=1,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("OpenSSH 'ssh' executable was not found on PATH") from exc
        assert self.process.stdout is not None
        super().__init__(self.process.stdout, args, label=label)

    def lifecycle_text(self, now: Optional[float] = None) -> str:
        now = time.monotonic() if now is None else now
        runtime = max(0.0, now - self.started_monotonic)
        code = self.process.poll()
        if code is None:
            return f"SSH PID {self.process.pid} · {runtime:.0f}s"
        return f"SSH EXIT {code} · {runtime:.0f}s"

    def _end_text(self) -> str:
        code = self.process.poll()
        return "SSH source disconnected" if code is None else f"SSH source exited with status {code}"

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.process.terminate(); self.process.wait(timeout=0.5)
            except Exception:
                try:
                    self.process.kill(); self.process.wait(timeout=0.5)
                except Exception:
                    pass
        if self.process.stdout is not None:
            try:
                self.process.stdout.close()
            except Exception:
                pass


class CompressedFollower:
    """Read a compressed file once. Compressed sources are intentionally static."""

    def __init__(self, path: Path, args) -> None:
        self.path = path
        self.args = args
        self.previous: List[str] = []
        self.initialized = False
        self._ended = False

    @property
    def finished(self) -> bool:
        return self._ended

    def lifecycle_text(self, now: Optional[float] = None) -> str:
        return "STATIC"

    def initialize_if_available(self) -> WatchNotice:
        try:
            self.previous = read_compressed_lines(self.path, self.args.encoding)
        except Exception as exc:
            self.initialized = True
            self._ended = True
            return WatchNotice("error", f"{self.path}: {exc}")
        self.initialized = True
        if self.args.lines is None:
            initial = list(self.previous)
        elif self.args.lines == 0:
            initial = []
        else:
            initial = list(self.previous[-self.args.lines:])
        return WatchNotice("initial", initial_tail=initial)

    def poll(self, now: Optional[float] = None):
        if not self.initialized:
            return self.initialize_if_available()
        if not self._ended:
            self._ended = True
            return WatchNotice("ended", "compressed static source loaded")
        return None

    def close(self) -> None:
        return
''')


# ---------------------------------------------------------------------------
# core.py: OSC-8 is zero-width ANSI for clipping/wrapping
# ---------------------------------------------------------------------------
path = "src/htail_app/core.py"
text = read(path)
text = replace_once(
    text,
    'ANSI_RE = re.compile(r"\\x1b\\[[0-9;?]*[ -/]*[@-~]")',
    'ANSI_RE = re.compile(r"(?:\\x1b\\][^\\x07\\x1b]*(?:\\x07|\\x1b\\\\)|\\x1b\\[[0-9;?]*[ -/]*[@-~])")',
    "OSC-aware ANSI regex",
)
write(path, text)


# ---------------------------------------------------------------------------
# input.py: horizontal arrows
# ---------------------------------------------------------------------------
path = "src/htail_app/input.py"
text = read(path)
text = replace_once(
    text,
    '        "\\x1b[A": "UP", "\\x1b[B": "DOWN", "\\x1b[5~": "PAGEUP",\n',
    '        "\\x1b[A": "UP", "\\x1b[B": "DOWN", "\\x1b[C": "RIGHT", "\\x1b[D": "LEFT", "\\x1b[5~": "PAGEUP",\n',
    "POSIX left/right map",
)
text = replace_once(
    text,
    '                return {"H": "UP", "P": "DOWN", "I": "PAGEUP", "Q": "PAGEDOWN", "G": "HOME", "O": "END"}.get(special)\n',
    '                return {"H": "UP", "P": "DOWN", "M": "RIGHT", "K": "LEFT", "I": "PAGEUP", "Q": "PAGEDOWN", "G": "HOME", "O": "END"}.get(special)\n',
    "Windows left/right map",
)
text = replace_once(
    text,
    'seq in ("\\x1b[A", "\\x1b[B", "\\x1b[H", "\\x1b[F", "\\x1bOH", "\\x1bOF", "\\x1b[Z")',
    'seq in ("\\x1b[A", "\\x1b[B", "\\x1b[C", "\\x1b[D", "\\x1b[H", "\\x1b[F", "\\x1bOH", "\\x1bOF", "\\x1b[Z")',
    "escape completion left/right",
)
write(path, text)


# ---------------------------------------------------------------------------
# pane.py: line numbers, nowrap/hscroll, rates, heartbeat, selected-search text
# ---------------------------------------------------------------------------
path = "src/htail_app/pane.py"
text = read(path)
text = replace_once(text, 'from collections import OrderedDict\n', 'from collections import OrderedDict, deque\n', "deque import")
text = replace_once(
    text,
    'from .searching import SEARCH_REGEX, SEARCH_SIMPLE, compile_search, search_label\n',
    'from .extras import linkify_urls\nfrom .searching import SEARCH_BOOLEAN, SEARCH_REGEX, SEARCH_SIMPLE, compile_search, search_label, simple_escape\n',
    "pane feature imports",
)
text = replace_once(
    text,
    '        display_name: Optional[str] = None,\n    ) -> None:\n',
    '        display_name: Optional[str] = None,\n        heartbeat_seconds: float = 0.0,\n    ) -> None:\n',
    "pane heartbeat ctor",
)
text = replace_once(
    text,
    '        self.message_until = 0.0\n\n        self._layout_dirty = True\n',
    '        self.message_until = 0.0\n        self.heartbeat_seconds = max(0.0, heartbeat_seconds)\n        self.source_status = ""\n        self.show_line_numbers = False\n        self.wrap_enabled = True\n        self.horizontal_offset = 0\n        self._activity = deque(maxlen=128)\n\n        self._layout_dirty = True\n',
    "pane feature state",
)
text = replace_once(
    text,
    '    def _wrap_cached(self, text: str, width: int) -> List[str]:\n        key = (max(1, width), text)\n',
    '    def _wrap_cached(self, text: str, width: int) -> List[str]:\n        if not self.wrap_enabled:\n            return [text]\n        key = (max(1, width), text)\n',
    "nowrap wrap cache",
)
text = replace_once(
    text,
    '        return self.highlighter.render_lines(raw_visible)\n\n    def _apply_regex_marks',
    '        return self.highlighter.render_lines(raw_visible)\n\n    @staticmethod\n    def _slice_ansi(text: str, start: int, width: int) -> str:\n        if start <= 0:\n            return core.clip_ansi(text, width)\n        visible = 0\n        raw = 0\n        while raw < len(text) and visible < start:\n            match = core.ANSI_RE.match(text, raw)\n            if match:\n                raw = match.end(); continue\n            raw += 1; visible += 1\n        return core.clip_ansi(text[raw:], width)\n\n    def _viewport_row(self, row: str, width: int) -> str:\n        row = linkify_urls(row, self.color)\n        if not self.wrap_enabled and self.horizontal_offset:\n            row = self._slice_ansi(row, self.horizontal_offset, width)\n        return _pad_ansi(row, width)\n\n    def _numbered_rows(self, row: str, width: int, number: Optional[int], total: int) -> List[str]:\n        if not self.show_line_numbers:\n            return self._wrap_cached(row, width)\n        digits = max(1, len(str(max(1, total))))\n        first = f"{number:>{digits}} │ " if number is not None else (" " * digits + " │ ")\n        cont = " " * digits + " │ "\n        content_width = max(1, width - len(first))\n        pieces = self._wrap_cached(row, content_width)\n        return [core.paint(first if i == 0 else cont, core.DIM, self.color) + piece for i, piece in enumerate(pieces)]\n\n    def _apply_regex_marks',
    "pane viewport helpers",
)
# Search target reveal and selected/current word helper.
text = replace_once(
    text,
    '    def _set_search_target(self, target: int) -> None:\n        self._search_last_target = target\n',
    '    def _set_search_target(self, target: int) -> None:\n        self._search_last_target = target\n',
    "search target anchor",
)
text = replace_once(
    text,
    '    def set_highlight(self, expression: str, flags: int = 0) -> Optional[str]:\n',
    '    def selected_search_text(self) -> str:\n        """Return the active selected match, or a useful word at the viewport."""\n        lines = self.snapshot_raw if self.snapshot_raw else [core.strip_ansi(line) for line in self.lines]\n        if not lines:\n            return ""\n        index = self._search_last_target\n        if index is None:\n            if self.snapshot_raw and self._snapshot_visual_to_source:\n                pos = min(max(0, self._snapshot_top), len(self._snapshot_visual_to_source) - 1)\n                index = self._snapshot_visual_to_source[pos]\n            if index is None:\n                index = min(max(0, self._logical_at_top()), len(lines) - 1)\n        if index is None or index < 0 or index >= len(lines):\n            return ""\n        plain = lines[index].rstrip("\\r\\n")\n        if self.search_regex is not None and self._search_last_target == index:\n            match = self.search_regex.search(plain)\n            if match is not None and match.end() > match.start():\n                return match.group(0)\n        match = re.search(r"[A-Za-z0-9_./:@+-]+", plain)\n        return match.group(0) if match else plain.strip().split()[0] if plain.strip() else ""\n\n    def search_selected(self, width: int, body_height: int) -> bool:\n        selected = self.selected_search_text()\n        if not selected:\n            self.set_message("nothing selected to search")\n            return False\n        error = self.set_search(simple_escape(selected), self.search_flags, mode=SEARCH_SIMPLE)\n        if error is not None:\n            self.set_message(error); return False\n        return self.select_search_match(0, width, body_height)\n\n    def set_highlight(self, expression: str, flags: int = 0) -> Optional[str]:\n',
    "search selected methods",
)
# Replace history layout wrapping.
text = replace_once(
    text,
    '        for logical_index, line in enumerate(self.lines):\n            self._logical_to_visual.append(len(self._visual_lines))\n            wrapped = self._wrap_cached(self._apply_regex_marks(line, logical_index), width)\n            self._visual_lines.extend(wrapped)\n            self._visual_to_logical.extend([logical_index] * len(wrapped))\n',
    '        for logical_index, line in enumerate(self.lines):\n            self._logical_to_visual.append(len(self._visual_lines))\n            marked = self._apply_regex_marks(line, logical_index)\n            wrapped = self._numbered_rows(marked, width, logical_index + 1, len(self.lines))\n            self._visual_lines.extend(wrapped)\n            self._visual_to_logical.extend([logical_index] * len(wrapped))\n',
    "history numbered layout",
)
# Replace snapshot header wrapping and source row wrapping with numbered helper.
text = text.replace('header_rows = self._wrap_cached(self.snapshot_update_header, width)', 'header_rows = self._numbered_rows(self.snapshot_update_header, width, None, len(self.snapshot_raw))')
if text.count('self._numbered_rows(self.snapshot_update_header, width, None, len(self.snapshot_raw))') != 2:
    raise RuntimeError("snapshot header numbering replacement count")
text = replace_once(
    text,
    '            wrapped_rows = self._wrap_cached(row, width)\n            visual.extend(wrapped_rows)\n',
    '            wrapped_rows = self._numbered_rows(row, width, source_index + 1, len(self.snapshot_raw))\n            visual.extend(wrapped_rows)\n',
    "snapshot numbered rows",
)
# Viewport row clipping for nowrap.
text = replace_once(
    text,
    '        return [_pad_ansi(row, width) for row in rows] + [" " * width] * max(0, height - len(rows))\n\n    def _viewport_counts',
    '        return [self._viewport_row(row, width) for row in rows] + [" " * width] * max(0, height - len(rows))\n\n    def _viewport_counts',
    "snapshot viewport slicing",
)
text = replace_once(
    text,
    '        return [_pad_ansi(row, width) for row in rows] + [" " * width] * max(0, height - len(rows))\n\n    def current_update_number',
    '        return [self._viewport_row(row, width) for row in rows] + [" " * width] * max(0, height - len(rows))\n\n    def current_update_number',
    "history viewport slicing",
)
# Insert monitoring and viewport toggles before toggle_follow_mode.
text = replace_once(
    text,
    '    def toggle_follow_mode(self) -> None:\n',
    '    def record_activity(self, line_count: int, byte_count: int, now: float) -> None:\n        self._activity.append((now, max(0, line_count), max(0, byte_count)))\n\n    def rate_text(self, now: Optional[float] = None) -> str:\n        now = time.monotonic() if now is None else now\n        recent = [sample for sample in self._activity if now - sample[0] <= 5.0]\n        if not recent:\n            return ""\n        lines = sum(sample[1] for sample in recent)\n        bytes_ = sum(sample[2] for sample in recent)\n        span = max(1.0, min(5.0, now - recent[0][0] + 1.0))\n        line_rate = lines / span\n        byte_rate = bytes_ / span\n        if line_rate < 0.05 and byte_rate < 1.0:\n            return ""\n        if byte_rate >= 1024 * 1024:\n            btext = f"{byte_rate / (1024*1024):.1f}MB/s"\n        elif byte_rate >= 1024:\n            btext = f"{byte_rate / 1024:.1f}KB/s"\n        else:\n            btext = f"{byte_rate:.0f}B/s"\n        return f"{line_rate:.1f}L/s · {btext}"\n\n    def cycle_heartbeat(self) -> None:\n        values = [0.0, 30.0, 60.0, 300.0, 600.0]\n        current = min(range(len(values)), key=lambda i: abs(values[i] - self.heartbeat_seconds))\n        self.heartbeat_seconds = values[(current + 1) % len(values)]\n        self.set_message("heartbeat off" if self.heartbeat_seconds == 0 else f"heartbeat {core.format_duration(self.heartbeat_seconds)}")\n\n    def toggle_line_numbers(self) -> None:\n        self.show_line_numbers = not self.show_line_numbers\n        self._mark_layout_dirty(); self._snapshot_layout_dirty = True\n        self.set_message("line numbers on" if self.show_line_numbers else "line numbers off")\n\n    def toggle_wrap(self) -> None:\n        self.wrap_enabled = not self.wrap_enabled\n        if self.wrap_enabled:\n            self.horizontal_offset = 0\n        self._mark_layout_dirty(); self._snapshot_layout_dirty = True\n        self.set_message("wrap on" if self.wrap_enabled else "wrap off · ←/→ scroll")\n\n    def scroll_horizontal(self, delta: int) -> None:\n        if self.wrap_enabled:\n            return\n        self.horizontal_offset = max(0, self.horizontal_offset + delta)\n\n    def toggle_follow_mode(self) -> None:\n',
    "monitoring and viewport methods",
)
# Title status additions.
text = replace_once(
    text,
    '        parts = [f"{index + 1}:{self.name}", state, self.follow_mode.upper()]\n',
    '        parts = [f"{index + 1}:{self.name}", state, self.follow_mode.upper()]\n        if self.source_status:\n            parts.append(self.source_status)\n        if self.show_line_numbers:\n            parts.append("LN")\n        if not self.wrap_enabled:\n            parts.append(f"NOWRAP ↔{self.horizontal_offset}")\n        rate = self.rate_text(now)\n        if rate:\n            parts.append(rate)\n',
    "title source/rate state",
)
text = replace_once(
    text,
    '        idle = self.idle_seconds(now)\n        if self.idle_warn > 0 and idle >= self.idle_warn:\n            parts.append(f"⚠ {core.format_duration(idle)}")\n',
    '        idle = self.idle_seconds(now)\n        if self.heartbeat_seconds > 0 and idle >= self.heartbeat_seconds:\n            parts.append(f"⚠ LATE {core.format_duration(idle - self.heartbeat_seconds)}")\n        elif self.idle_warn > 0 and idle >= self.idle_warn:\n            parts.append(f"⚠ {core.format_duration(idle)}")\n',
    "heartbeat title alert",
)
# StreamPane super call is compatible because heartbeat defaults.
write(path, text)


# ---------------------------------------------------------------------------
# app.py: palette/outline, source factories, Boolean mode, monitoring, controls
# ---------------------------------------------------------------------------
path = "src/htail_app/app.py"
text = read(path)
text = replace_once(text, 'import argparse\n', 'import argparse\nfrom dataclasses import dataclass\n', "dataclass import")
text = replace_once(
    text,
    'from .pane import Pane, StreamPane\nfrom .searching import GlobalSearchMatch, SEARCH_REGEX, SEARCH_SIMPLE, compile_search, preview_around_match, search_label\nfrom .sources import CommandFollower, StreamFollower\n',
    'from .pane import Pane, StreamPane\nfrom .extras import is_compressed_path, markdown_outline, parse_duration, syntax_path_for_source\nfrom .searching import GlobalSearchMatch, SEARCH_BOOLEAN, SEARCH_REGEX, SEARCH_SIMPLE, compile_search, preview_around_match, search_label\nfrom .sources import CommandFollower, CompressedFollower, SSHFollower, StreamFollower\n',
    "app feature imports",
)
# Add palette item dataclass after constants.
text = replace_once(
    text,
    'GLOBAL_SEARCH_LIMIT = 250\n\n\ndef executable_path',
    'GLOBAL_SEARCH_LIMIT = 250\n\n\n@dataclass(frozen=True)\nclass PaletteItem:\n    label: str\n    action: str\n    value: object = None\n    detail: str = ""\n\n\ndef executable_path',
    "palette item dataclass",
)
# Parser args.
text = replace_once(
    text,
    '    parser.add_argument("--exec", dest="commands", action="append", default=[], metavar="COMMAND", help="run a shell command and watch its merged stdout/stderr; repeatable")\n',
    '    parser.add_argument("--exec", dest="commands", action="append", default=[], metavar="COMMAND", help="run a shell command and watch its merged stdout/stderr; repeatable")\n    parser.add_argument("--ssh", dest="ssh_sources", action="append", default=[], metavar="SOURCE", help="follow remote SOURCE via OpenSSH: user@host:/path or ssh://host/path; repeatable")\n',
    "ssh parser arg",
)
text = replace_once(
    text,
    '    parser.add_argument("--idle-warn", type=float, default=300.0, metavar="SECONDS")\n',
    '    parser.add_argument("--idle-warn", type=float, default=300.0, metavar="SECONDS")\n    parser.add_argument("--heartbeat", type=parse_duration, default=0.0, metavar="DURATION", help="expected update heartbeat, e.g. 30s, 5m, 1h or off")\n',
    "heartbeat parser arg",
)
# Search state type and palette state in __init__.
text = replace_once(
    text,
    '        self.prompt_restore_state: Optional[Tuple[str, str, Optional[int]]] = None\n',
    '        self.prompt_restore_state: Optional[Tuple[str, str, int, Optional[int]]] = None\n',
    "search state annotation",
)
text = replace_once(
    text,
    '        self.global_search_truncated = False\n        self._last_frame: Optional[List[str]] = None\n',
    '        self.global_search_truncated = False\n        self.palette_active = False\n        self.palette_mode = "commands"\n        self.palette_buffer = ""\n        self.palette_selected = 0\n        self.palette_items: List[PaletteItem] = []\n        self._last_frame: Optional[List[str]] = None\n',
    "palette state",
)
# Pane constructor heartbeat - replace occurrences in MultiApp source creation and dynamic; tests default elsewhere.
text = text.replace('Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name="stdin")', 'Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name="stdin", heartbeat_seconds=args.heartbeat)')
text = text.replace('Pane(path, highlighter, display_filter, color, args.idle_warn)', 'Pane(path, highlighter, display_filter, color, args.idle_warn, heartbeat_seconds=args.heartbeat)')
text = text.replace('Pane(path, highlighter, self.display_filter, self.color, self.args.idle_warn)', 'Pane(path, highlighter, self.display_filter, self.color, self.args.idle_warn, heartbeat_seconds=self.args.heartbeat)')
text = text.replace('Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name=f"$ {command}")', 'Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name=f"$ {command}", heartbeat_seconds=args.heartbeat)')
text = text.replace('Pane(pseudo, highlighter, self.display_filter, self.color, self.args.idle_warn, display_name=label)', 'Pane(pseudo, highlighter, self.display_filter, self.color, self.args.idle_warn, display_name=label, heartbeat_seconds=self.args.heartbeat)')
# File source in MultiApp initial: highlighter/follower selection.
text = replace_once(
    text,
    '            else:\n                highlighter = core.SyntaxHighlighter(path, args.syntax, color)\n                pane = Pane(path, highlighter, display_filter, color, args.idle_warn, heartbeat_seconds=args.heartbeat)\n                follower = FileFollower(path, args)\n',
    '            else:\n                highlighter = core.SyntaxHighlighter(syntax_path_for_source(path), args.syntax, color)\n                pane = Pane(path, highlighter, display_filter, color, args.idle_warn, heartbeat_seconds=args.heartbeat)\n                follower = CompressedFollower(path, args) if is_compressed_path(path) else FileFollower(path, args)\n',
    "initial compressed source",
)
# Only native-watch true files.
text = replace_once(
    text,
    '                self._known_file_paths.add(Path(os.path.abspath(os.fspath(path))))\n                self.native_watch.add_file(path)\n',
    '                self._known_file_paths.add(Path(os.path.abspath(os.fspath(path))))\n                if isinstance(follower, FileFollower):\n                    self.native_watch.add_file(path)\n',
    "initial native watch conditional",
)
# Dynamic source compressed.
text = replace_once(
    text,
    '        highlighter = core.SyntaxHighlighter(path, self.args.syntax, self.color)\n        pane = Pane(path, highlighter, self.display_filter, self.color, self.args.idle_warn, heartbeat_seconds=self.args.heartbeat)\n        follower = FileFollower(path, self.args)\n',
    '        highlighter = core.SyntaxHighlighter(syntax_path_for_source(path), self.args.syntax, self.color)\n        pane = Pane(path, highlighter, self.display_filter, self.color, self.args.idle_warn, heartbeat_seconds=self.args.heartbeat)\n        follower = CompressedFollower(path, self.args) if is_compressed_path(path) else FileFollower(path, self.args)\n',
    "dynamic compressed source",
)
text = replace_once(
    text,
    '        self._known_file_paths.add(normalized)\n        self.native_watch.add_file(path)\n',
    '        self._known_file_paths.add(normalized)\n        if isinstance(follower, FileFollower):\n            self.native_watch.add_file(path)\n',
    "dynamic native watch conditional",
)
# Add SSH panes after command loop in MultiApp.
anchor = '            self.panes.append(pane)\n            self.followers.append(follower)\n\n    def _add_dynamic_file'
ssh_block = '''            self.panes.append(pane)\n            self.followers.append(follower)\n\n        for source in args.ssh_sources:\n            follower = SSHFollower(source, args)\n            pseudo = Path("ssh.log")\n            highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)\n            pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name=follower.label, heartbeat_seconds=args.heartbeat)\n            follower.initialize_if_available()\n            pane.set_message(f"connected process pid {follower.process.pid}", 4.0)\n            self.panes.append(pane)\n            self.followers.append(follower)\n\n    def _add_dynamic_file'''
text = replace_once(text, anchor, ssh_block, "interactive SSH panes")
# Search mode cycle + names.
text = replace_once(
    text,
    '    def _other_search_mode(mode: str) -> str:\n        return SEARCH_REGEX if mode == SEARCH_SIMPLE else SEARCH_SIMPLE\n',
    '    def _other_search_mode(mode: str) -> str:\n        return {SEARCH_SIMPLE: SEARCH_REGEX, SEARCH_REGEX: SEARCH_BOOLEAN, SEARCH_BOOLEAN: SEARCH_SIMPLE}.get(mode, SEARCH_SIMPLE)\n',
    "three search modes",
)
text = replace_once(
    text,
    '    def _search_mode_name(mode: str) -> str:\n        return "Simple" if mode == SEARCH_SIMPLE else "Regex"\n',
    '    def _search_mode_name(mode: str) -> str:\n        return {SEARCH_SIMPLE: "Simple", SEARCH_REGEX: "Regex", SEARCH_BOOLEAN: "Boolean"}.get(mode, mode.title())\n',
    "search mode names",
)
# Add palette methods before _prompt_lines.
text = replace_once(
    text,
    '    def _prompt_lines(self, width: int, height: int) -> List[str]:\n',
    r'''    def _palette_all_items(self) -> List[PaletteItem]:
        pane = self.active_pane()
        if self.palette_mode == "outline":
            return [
                PaletteItem(("  " * max(0, entry.level - 1)) + f"{entry.text}", "outline-jump", entry.source_index, f"line {entry.source_index + 1}")
                for entry in markdown_outline(pane.snapshot_raw)
            ]
        items = [
            PaletteItem("Markdown outline", "outline", detail="jump to a heading"),
            PaletteItem("Toggle wrap", "wrap", detail="wrap / horizontal scrolling"),
            PaletteItem("Toggle line numbers", "line-numbers"),
            PaletteItem("Cycle expected heartbeat", "heartbeat", detail="off → 30s → 1m → 5m → 10m"),
            PaletteItem("Search selected match / current word", "search-selected"),
            PaletteItem("Toggle CHANGES / TAIL follow mode", "follow"),
            PaletteItem("Clear active search", "clear-search"),
        ]
        items.extend(PaletteItem(f"Focus pane {i + 1}: {candidate.name}", "focus", i) for i, candidate in enumerate(self.panes))
        return items

    @staticmethod
    def _palette_matches(label: str, query: str) -> bool:
        words = [word.lower() for word in query.split() if word]
        target = label.lower()
        return all(word in target for word in words)

    def _refresh_palette(self) -> None:
        all_items = self._palette_all_items()
        self.palette_items = [item for item in all_items if self._palette_matches(item.label + " " + item.detail, self.palette_buffer)]
        if self.palette_items:
            self.palette_selected = min(max(0, self.palette_selected), len(self.palette_items) - 1)
        else:
            self.palette_selected = 0

    def _open_palette(self) -> None:
        self.palette_active = True
        self.palette_mode = "commands"
        self.palette_buffer = ""
        self.palette_selected = 0
        self._refresh_palette()
        self.dirty = True

    def _execute_palette_item(self) -> None:
        self._refresh_palette()
        if not self.palette_items:
            return
        item = self.palette_items[self.palette_selected]
        pane = self.active_pane()
        inner_w, body_h = self._active_pane_geometry()
        if item.action == "outline":
            self.palette_mode = "outline"; self.palette_buffer = ""; self.palette_selected = 0; self._refresh_palette()
            if not self.palette_items:
                self.set_message("no Markdown headings found")
                self.palette_active = False
            return
        if item.action == "outline-jump":
            pane.jump_to_source_line(int(item.value), inner_w, body_h)
        elif item.action == "wrap":
            pane.toggle_wrap()
        elif item.action == "line-numbers":
            pane.toggle_line_numbers()
        elif item.action == "heartbeat":
            pane.cycle_heartbeat()
        elif item.action == "search-selected":
            pane.search_selected(inner_w, body_h)
        elif item.action == "follow":
            pane.toggle_follow_mode()
        elif item.action == "clear-search":
            pane.set_search("", pane.search_flags, mode=pane.search_mode)
            pane.set_message("search cleared")
        elif item.action == "focus":
            self.focus = int(item.value)
        self.palette_active = False
        self.dirty = True

    def _palette_lines(self, width: int, height: int) -> List[str]:
        self._refresh_palette()
        title = "Markdown outline" if self.palette_mode == "outline" else "Command palette"
        content = [core.paint("> " + self.palette_buffer + "▌", core.BOLD_LIGHT_CYAN, self.color), ""]
        if not self.palette_items:
            content.append(core.paint("No matches", core.DIM, self.color))
        else:
            start = max(0, min(self.palette_selected - 5, max(0, len(self.palette_items) - 10)))
            for index, item in enumerate(self.palette_items[start:start + 10], start=start):
                prefix = "› " if index == self.palette_selected else "  "
                row = prefix + item.label
                if item.detail:
                    row += "  ·  " + item.detail
                if index == self.palette_selected:
                    row = core.paint(row, "\x1b[1;30;106m", self.color)
                content.append(row)
        content.extend(["", "↑/↓ select · Enter apply · Esc close · type to filter"])
        return _panel_lines(title, content, width, height, self.color)

    def _prompt_lines(self, width: int, height: int) -> List[str]:
''',
    "palette methods",
)
# Adjust prompt docs for Boolean mode.
text = replace_once(
    text,
    '            if self.prompt_search_mode == SEARCH_SIMPLE:\n                content.append("Simple: ordinary text is literal · * any text · ? one character")\n            else:\n                content.append("Regex: Python regular-expression syntax")\n',
    '            if self.prompt_search_mode == SEARCH_SIMPLE:\n                content.append("Simple: ordinary text is literal · * any text · ? one character")\n            elif self.prompt_search_mode == SEARCH_REGEX:\n                content.append("Regex: Python regular-expression syntax")\n            else:\n                content.append("Boolean: AND / OR / NOT, parentheses and quoted phrases; terms use Simple semantics")\n',
    "Boolean prompt help",
)
# Frame overlay palette + status.
text = replace_once(
    text,
    '        if self.global_search_active:\n            body = _overlay_modal(base_body, self._global_search_lines(width, body_height), width, body_height, self.color)\n',
    '        if self.palette_active:\n            body = _overlay_modal(base_body, self._palette_lines(width, body_height), width, body_height, self.color)\n        elif self.global_search_active:\n            body = _overlay_modal(base_body, self._global_search_lines(width, body_height), width, body_height, self.color)\n',
    "palette overlay",
)
text = replace_once(
    text,
    '        if self.global_search_active:\n            status = [f"GLOBAL SEARCH · {self._search_mode_name(self.global_search_mode)} · ↑↓ select · Enter jump · Tab mode · Esc close", "Background watching continues while this dialog is open"]\n',
    '        if self.palette_active:\n            status = ["COMMAND PALETTE · type to filter · ↑↓ select · Enter apply · Esc close", "Background watching continues while this dialog is open"]\n        elif self.global_search_active:\n            status = [f"GLOBAL SEARCH · {self._search_mode_name(self.global_search_mode)} · ↑↓ select · Enter jump · Tab mode · Esc close", "Background watching continues while this dialog is open"]\n',
    "palette status",
)
# General controls footer.
text = replace_once(
    text,
    '        controls = "/ search · g global · n/N match · h highlight · Tab pane · l layout · ↑↓/Pg scroll · [/] update · f newest · t follow · p pause · u update · q quit · ? help"\n',
    '        controls = ": commands · / search · g global · * selected · n/N match · Tab pane · ↑↓ scroll · ←→ hscroll · [/] update · f newest · u update · ? help"\n',
    "footer controls",
)
# Palette input block before global search.
text = replace_once(
    text,
    '    def handle_input(self, event: InputEvent) -> bool:\n        if self.global_search_active and not isinstance(event, MouseEvent):\n',
    '''    def handle_input(self, event: InputEvent) -> bool:\n        if self.palette_active and not isinstance(event, MouseEvent):\n            key = event\n            if key == "ESC":\n                self.palette_active = False; self.dirty = True; return False\n            if key in ("UP", "DOWN", "PAGEUP", "PAGEDOWN"):\n                self._refresh_palette()\n                if self.palette_items:\n                    delta = {"UP": -1, "DOWN": 1, "PAGEUP": -8, "PAGEDOWN": 8}[key]\n                    self.palette_selected = min(max(0, self.palette_selected + delta), len(self.palette_items) - 1)\n                self.dirty = True; return False\n            if key in ("\\r", "\\n"):\n                self._execute_palette_item(); self.dirty = True; return False\n            if key in ("\\x7f", "\\b"):\n                self.palette_buffer = self.palette_buffer[:-1]; self.palette_selected = 0; self._refresh_palette(); self.dirty = True; return False\n            if isinstance(key, str) and len(key) == 1 and key.isprintable():\n                self.palette_buffer += key; self.palette_selected = 0; self._refresh_palette(); self.dirty = True\n            return False\n\n        if self.global_search_active and not isinstance(event, MouseEvent):\n''',
    "palette input",
)
# Mouse modal guard includes palette.
text = replace_once(
    text,
    '            if not (self.help_active or self.layout_menu or self.update_confirm_active or self.global_search_active or self.prompt_mode):\n',
    '            if not (self.help_active or self.layout_menu or self.update_confirm_active or self.global_search_active or self.palette_active or self.prompt_mode):\n',
    "palette mouse guard",
)
# Key ':' and '*' before search.
text = replace_once(
    text,
    '        if key == "/":\n',
    '        if key == ":":\n            self._open_palette(); return False\n        if key == "*":\n            pane = self.active_pane(); inner_w, body_h = self._active_pane_geometry(); pane.search_selected(inner_w, body_h); self.dirty = True; return False\n\n        if key == "/":\n',
    "palette/search-selected keys",
)
# Horizontal arrow handling.
text = replace_once(
    text,
    '        if key in ("UP", "DOWN", "PAGEUP", "PAGEDOWN", "HOME", "END"):\n',
    '        if key in ("LEFT", "RIGHT"):\n            pane.scroll_horizontal(-4 if key == "LEFT" else 4); self.dirty = True; return False\n        if key in ("UP", "DOWN", "PAGEUP", "PAGEDOWN", "HOME", "END"):\n',
    "horizontal key handling",
)
# Tick lifecycle status.
text = replace_once(
    text,
    '    def tick(self, now: float) -> None:\n        self._tick_updates(now)\n',
    '    def tick(self, now: float) -> None:\n        self._tick_updates(now)\n        for pane, follower in zip(self.panes, self.followers):\n            lifecycle = getattr(follower, "lifecycle_text", None)\n            status = lifecycle(now) if callable(lifecycle) else ""\n            if pane.source_status != status:\n                pane.source_status = status\n                self.dirty = True\n',
    "lifecycle tick",
)
# Activity record on update.
text = replace_once(
    text,
    '            if isinstance(result, WatchUpdate):\n                if pane.missing:\n',
    '            if isinstance(result, WatchUpdate):\n                byte_count = sum(len(line.encode(self.args.encoding, errors="replace")) for kind, lines in result.events if kind != "delete" for line in lines)\n                pane.record_activity(result.added + result.replaced, byte_count, now)\n                if pane.missing:\n',
    "rate activity",
)
# Help lines additions.
text = replace_once(
    text,
    '            "Focused pane",\n            "  /                  search focused pane; Tab toggles Simple / Regex",\n',
    '            "Focused pane",\n            "  :                  command palette / Markdown outline",\n            "  /                  search focused pane; Tab cycles Simple / Regex / Boolean",\n            "  *                  search selected match / current word",\n',
    "help palette/search modes",
)
text = replace_once(
    text,
    '            "  ↑ ↓ / PgUp PgDn    scroll",\n',
    '            "  ↑ ↓ / PgUp PgDn    vertical scroll",\n            "  ← →                horizontal scroll when wrap is off",\n',
    "help horizontal",
)
# Noninteractive compressed source.
text = replace_once(
    text,
    '        else:\n            highlighter = core.SyntaxHighlighter(path, args.syntax, color)\n            pane = Pane(path, highlighter, display_filter, color, args.idle_warn, heartbeat_seconds=args.heartbeat)\n            follower = FileFollower(path, args)\n',
    '        else:\n            highlighter = core.SyntaxHighlighter(syntax_path_for_source(path), args.syntax, color)\n            pane = Pane(path, highlighter, display_filter, color, args.idle_warn, heartbeat_seconds=args.heartbeat)\n            follower = CompressedFollower(path, args) if is_compressed_path(path) else FileFollower(path, args)\n',
    "noninteractive compressed source",
)
# Noninteractive SSH after commands loop.
anchor = '        if not args.no_start_banner:\n            print(f"[htail {VERSION}] [{len(panes)}] running {command} (pid {follower.process.pid})")\n\n    next_glob_scan = 0.0\n'
replacement = '''        if not args.no_start_banner:\n            print(f"[htail {VERSION}] [{len(panes)}] running {command} (pid {follower.process.pid})")\n\n    for source in args.ssh_sources:\n        follower = SSHFollower(source, args)\n        pseudo = Path("ssh.log")\n        highlighter = core.SyntaxHighlighter(pseudo, args.syntax, color)\n        pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name=follower.label, heartbeat_seconds=args.heartbeat)\n        follower.initialize_if_available()\n        panes.append(pane); followers.append(follower)\n        if not args.no_start_banner:\n            print(f"[htail {VERSION}] [{len(panes)}] following {follower.label} (pid {follower.process.pid})")\n\n    next_glob_scan = 0.0\n'''
text = replace_once(text, anchor, replacement, "noninteractive SSH")
# Main source existence and interactivity include SSH.
text = replace_once(
    text,
    '    if not args.files and not args.commands and not args.globs and not sys.stdin.isatty():\n',
    '    if not args.files and not args.commands and not args.ssh_sources and not args.globs and not sys.stdin.isatty():\n',
    "stdin default ssh awareness",
)
text = replace_once(
    text,
    '    if not args.files and not args.commands and not args.globs:\n',
    '    if not args.files and not args.commands and not args.ssh_sources and not args.globs:\n',
    "usage ssh awareness",
)
text = replace_once(
    text,
    '        print("Usage: ht FILE [FILE ...] | ht --glob \'logs/*.log\' | producer | ht | ht --exec COMMAND")\n',
    '        print("Usage: ht FILE [FILE ...] | ht --glob \'logs/*.log\' | ht --ssh user@host:/path | producer | ht | ht --exec COMMAND")\n',
    "usage ssh text",
)
text = replace_once(
    text,
    '    interactive = sys.stdout.isatty() and (sys.stdin.isatty() or has_stdin_source or bool(args.commands) or bool(args.globs))\n',
    '    interactive = sys.stdout.isatty() and (sys.stdin.isatty() or has_stdin_source or bool(args.commands) or bool(args.ssh_sources) or bool(args.globs))\n',
    "interactive ssh awareness",
)
write(path, text)


# ---------------------------------------------------------------------------
# Version/docs/release notes
# ---------------------------------------------------------------------------
path = "src/htail_app/__init__.py"
text = read(path).replace('VERSION = "0.14.0"', 'VERSION = "0.15.0"')
write(path, text)

path = "README.md"
text = read(path)
text += r'''

### 0.15 inspection and source features

Press `:` for the command palette. It includes a Markdown heading outline, per-pane wrap and line-number toggles, heartbeat configuration, follow mode, search clearing, search-selected/current-word, and pane switching. The outline jumps directly to headings in the current Markdown snapshot.

Search now has three modes: **Simple**, **Regex**, and **Boolean**. `Tab` cycles them. Boolean mode accepts `AND`, `OR`, `NOT`, parentheses, quoted phrases, and implicit AND; individual terms use the same friendly wildcard semantics as Simple search. `*` searches the active selected match, or a useful token at the current viewport when there is no selected match.

Wrap is per pane. With wrap disabled, `←` / `→` scroll horizontally and the pane title reports the horizontal offset. Line numbers are also per pane and can be toggled from the command palette.

`--heartbeat 5m` sets an expected update interval. A source that exceeds it is marked `LATE`; per-pane heartbeat can be cycled from the command palette. Active sources also show a rolling line/byte rate. `--exec` and `--ssh` panes display PID, runtime, and exit status.

Compressed `.gz`, `.bz2`, `.xz`, and `.lzma` files can be opened directly as **static** sources. Remote files can be followed with the system OpenSSH client using `--ssh user@host:/path` or `--ssh ssh://user@host/path`; existing SSH config, keys, agents, ProxyJump, and host-key policy remain owned by OpenSSH.

Visible `http://` and `https://` URLs are emitted as OSC-8 terminal hyperlinks, so supporting terminals can open them directly (typically Ctrl/Cmd-click depending on the terminal).
'''
write(path, text)

write("RELEASE_NOTES.md", r'''# htail 0.15.0

## New features

- Added a searchable `:` command palette with a Markdown heading outline, pane switching and configuration actions.
- Added Boolean search (`AND`, `OR`, `NOT`, parentheses and quoted phrases) as a third search mode alongside Simple and Regex.
- Added `*` search-selected/current-word behavior.
- Added per-pane line numbers and wrap-off mode with horizontal `←` / `→` scrolling.
- Added rolling line/byte rate meters and configurable expected-heartbeat alerts (`--heartbeat 5m`).
- Added direct static viewing of `.gz`, `.bz2`, `.xz` and `.lzma` files.
- Added first-class OpenSSH remote-tail sources via `--ssh user@host:/path` or `--ssh ssh://host/path`.
- `--exec` and SSH source panes now expose process PID, runtime and exit status in their lifecycle state.
- HTTP(S) URLs are emitted as OSC-8 hyperlinks for terminals that support clickable links.

## Notes

- Compressed inputs are intentionally static snapshots; they are not re-read when the compressed file changes.
- SSH transport/authentication uses the installed `ssh` command and therefore respects the user's normal OpenSSH configuration.
''')

# ---------------------------------------------------------------------------
# Regression tests for the 0.15 slices
# ---------------------------------------------------------------------------
write("tests/test_features_015.py", r'''from __future__ import annotations

from argparse import Namespace
import gzip
from pathlib import Path
import re
import tempfile
import time
import unittest

from htail_app import app, core
from htail_app.extras import markdown_outline, parse_duration, parse_ssh_source
from htail_app.pane import Pane
from htail_app.searching import SEARCH_BOOLEAN, SEARCH_SIMPLE, compile_search
from htail_app.sources import CompressedFollower


class BooleanSearchTests(unittest.TestCase):
    def test_boolean_and_or_not_and_quotes(self):
        pattern, error = compile_search('ERROR AND (retry OR "connection lost") AND NOT harmless', SEARCH_BOOLEAN, re.I)
        self.assertIsNone(error)
        self.assertIsNotNone(pattern.search('error: retry connection'))
        self.assertIsNotNone(pattern.search('ERROR connection lost'))
        self.assertIsNone(pattern.search('ERROR retry harmless'))
        self.assertIsNone(pattern.search('retry only'))

    def test_implicit_and(self):
        pattern, error = compile_search('alpha beta', SEARCH_BOOLEAN)
        self.assertIsNone(error)
        self.assertIsNotNone(pattern.search('alpha xx beta'))
        self.assertIsNone(pattern.search('alpha only'))


class OutlineAndDurationTests(unittest.TestCase):
    def test_outline_ignores_fenced_headings(self):
        entries = markdown_outline(['# One\n', '```\n', '# fake\n', '```\n', '### Three\n'])
        self.assertEqual([(e.level, e.source_index, e.text) for e in entries], [(1, 0, 'One'), (3, 4, 'Three')])

    def test_duration_parser(self):
        self.assertEqual(parse_duration('5m'), 300.0)
        self.assertEqual(parse_duration('1.5h'), 5400.0)
        self.assertEqual(parse_duration('off'), 0.0)


class SourceTests(unittest.TestCase):
    def args(self, lines=None):
        return Namespace(encoding='utf-8', lines=lines)

    def test_compressed_gzip_is_static_initial_source(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'x.log.gz'
            with gzip.open(path, 'wt', encoding='utf-8') as handle:
                handle.write('one\ntwo\n')
            follower = CompressedFollower(path, self.args())
            notice = follower.initialize_if_available()
            self.assertEqual(notice.initial_tail, ['one\n', 'two\n'])
            ended = follower.poll()
            self.assertEqual(ended.kind, 'ended')
            self.assertTrue(follower.finished)

    def test_ssh_parser_uses_system_ssh_and_remote_tail(self):
        argv, label = parse_ssh_source('user@example.com:/var/log/app.log')
        self.assertEqual(argv[0], 'ssh')
        self.assertIn('user@example.com', argv)
        self.assertIn('tail -F -- /var/log/app.log', argv[-1])
        self.assertIn('/var/log/app.log', label)


class PaneFeatureTests(unittest.TestCase):
    def make_pane(self, color=False):
        path = Path('sample.md')
        pane = Pane(path, core.SyntaxHighlighter(path, 'none', color), core.DisplayFilter(), color, 0.0, heartbeat_seconds=2.0)
        rows = ['# Heading\n', 'alpha target https://example.com/very/long/path\n', 'beta target\n']
        pane.add_initial(rows)
        pane.set_snapshot(rows)
        return pane

    def test_line_numbers_and_nowrap_horizontal_scroll(self):
        pane = self.make_pane(False)
        pane.toggle_line_numbers()
        pane.toggle_wrap()
        pane.scroll_horizontal(8)
        box = '\n'.join(core.strip_ansi(row) for row in pane.render_box(30, 6, True, 0))
        self.assertIn('LN', box.splitlines()[0])
        self.assertIn('NOWRAP', box.splitlines()[0])
        self.assertEqual(pane.horizontal_offset, 8)

    def test_rate_and_heartbeat_status(self):
        pane = self.make_pane(False)
        now = time.monotonic()
        pane.watch_started_monotonic = now - 5.0
        pane.record_activity(20, 4096, now)
        title = core.strip_ansi(pane.title(0, 120, True, 4))
        self.assertIn('L/s', title)
        pane._activity.clear()
        title = core.strip_ansi(pane.title(0, 120, True, 4))
        self.assertIn('LATE', title)

    def test_search_selected_reuses_current_match(self):
        pane = self.make_pane(False)
        pane.set_search('target', mode=SEARCH_SIMPLE)
        pane.select_search_match(0, 60, 4)
        self.assertEqual(pane.selected_search_text(), 'target')

    def test_linkified_url_is_zero_width_for_strip(self):
        pane = self.make_pane(True)
        pane.render_box(100, 6, True, 0)
        rendered = '\n'.join(pane._visual_lines)
        self.assertIn('\x1b]8;;https://example.com/very/long/path', rendered)
        self.assertNotIn('\x1b]8;;', core.strip_ansi(rendered))


class PaletteAndParserTests(unittest.TestCase):
    def test_parser_accepts_heartbeat_and_ssh(self):
        args = app.parse_args(['--heartbeat', '5m', '--ssh', 'host:/tmp/a.log'])
        self.assertEqual(args.heartbeat, 300.0)
        self.assertEqual(args.ssh_sources, ['host:/tmp/a.log'])

    def test_palette_contains_requested_actions(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'x.md'
            path.write_text('# Alpha\ntext\n## Beta\n', encoding='utf-8')
            args = app.parse_args([str(path), '--no-native-watch', '--no-color'])
            application = app.MultiApp(args, False, core.DisplayFilter(), core.UpdateService(''))
            try:
                application._open_palette()
                labels = [item.label for item in application.palette_items]
                self.assertIn('Markdown outline', labels)
                self.assertIn('Toggle wrap', labels)
                self.assertIn('Toggle line numbers', labels)
                outline = next(i for i, item in enumerate(application.palette_items) if item.action == 'outline')
                application.palette_selected = outline
                application._execute_palette_item()
                self.assertEqual(application.palette_mode, 'outline')
                self.assertTrue(any('Alpha' in item.label for item in application.palette_items))
            finally:
                application.close_native_watch()


if __name__ == '__main__':
    unittest.main()
''')

# Existing version assertions, if any.
for test_path in Path("tests").glob("test_*.py"):
    data = test_path.read_text(encoding="utf-8")
    data = data.replace('0.14.0', '0.15.0')
    test_path.write_text(data, encoding="utf-8")
