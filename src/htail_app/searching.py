from __future__ import annotations

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
