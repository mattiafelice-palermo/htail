from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional, Pattern, Tuple

SEARCH_SIMPLE = "simple"
SEARCH_REGEX = "regex"
SEARCH_MODES = (SEARCH_SIMPLE, SEARCH_REGEX)


@dataclass(frozen=True)
class GlobalSearchMatch:
    pane_index: int
    source_index: int
    pane_name: str
    text: str
    match_start: int
    match_end: int


def simple_pattern_to_regex(expression: str) -> str:
    """Translate shell-like search wildcards while keeping all other text literal.

    `*` matches any text within one source line, `?` matches one character, and
    a backslash escapes the following character. Unlike fnmatch, the result is
    intentionally not anchored so ordinary text behaves as substring search.
    """
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


def compile_search(expression: str, mode: str, flags: int = 0) -> Tuple[Optional[Pattern[str]], Optional[str]]:
    if not expression:
        return None, None
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
    return f"/{expression}/" if mode == SEARCH_REGEX else expression


def preview_around_match(text: str, start: int, end: int, limit: int) -> tuple[str, int, int]:
    """Return a compact preview plus match coordinates inside that preview."""
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
        pstart = max(0, pstart)
        pend = max(pstart, pend)
    if right < len(text):
        preview = preview[:-1] + "…"
    return preview, pstart, pend
