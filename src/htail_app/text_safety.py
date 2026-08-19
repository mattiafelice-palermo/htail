"""Bounded text classification and terminal-safe source sanitization."""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from pathlib import Path
import unicodedata
from typing import List, Optional, Tuple


CLASSIFIER_SAMPLE_BYTES = 64 * 1024

_CONTROL_PICTURES = {
    0x00: "␀",
    0x01: "␁",
    0x02: "␂",
    0x03: "␃",
    0x04: "␄",
    0x05: "␅",
    0x06: "␆",
    0x07: "␇",
    0x08: "␈",
    0x09: "\t",
    0x0A: "\n",
    0x0B: "␋",
    0x0C: "␌",
    0x0D: "\r",
    0x0E: "␎",
    0x0F: "␏",
    0x10: "␐",
    0x11: "␑",
    0x12: "␒",
    0x13: "␓",
    0x14: "␔",
    0x15: "␕",
    0x16: "␖",
    0x17: "␗",
    0x18: "␘",
    0x19: "␙",
    0x1A: "␚",
    0x1B: "␛",
    0x1C: "␜",
    0x1D: "␝",
    0x1E: "␞",
    0x1F: "␟",
    0x7F: "␡",
}

_GRAPHEME_FORMAT_CONTROLS = {0x200C, 0x200D}


def _needs_unicode_escape(char: str) -> bool:
    """Return whether an invisible Unicode character needs visible escaping."""

    codepoint = ord(char)
    if codepoint in _GRAPHEME_FORMAT_CONTROLS:
        # ZWNJ and ZWJ are part of legitimate script shaping and emoji
        # sequences. They are not terminal controls and must remain available
        # to the grapheme-width model.
        return False
    category = unicodedata.category(char)
    return category in {"Cf", "Cn", "Cs", "Zl", "Zp"}


@dataclass(frozen=True)
class TextInspection:
    """Deterministic metrics from a bounded source-byte sample."""

    suspicious: bool
    reason: str = ""
    sample_bytes: int = 0
    decoded_chars: int = 0
    printable_density: float = 1.0
    control_density: float = 0.0
    nul_density: float = 0.0
    decode_error: Optional[str] = None


def _encoding_name(encoding: str) -> str:
    try:
        return codecs.lookup(encoding).name.lower().replace("_", "-")
    except LookupError:
        return encoding.strip().lower().replace("_", "-")


def _is_utf_family(encoding: str) -> bool:
    name = _encoding_name(encoding)
    return name.startswith(("utf-16", "utf-32"))


def _strict_incremental_decode(payload: bytes, encoding: str) -> tuple[str, Optional[str]]:
    try:
        decoder_factory = codecs.getincrementaldecoder(encoding)
        decoder = decoder_factory(errors="strict")
        # final=False deliberately permits a valid multibyte character to be
        # completed by bytes just beyond the bounded sample.
        return decoder.decode(payload, final=False), None
    except (LookupError, UnicodeDecodeError) as exc:
        return "", str(exc)


def inspect_bytes(payload: bytes, encoding: str = "utf-8") -> TextInspection:
    """Classify at most ``payload`` bytes using content and selected encoding.

    A strict incremental decode is the primary signal. Decoded control and
    printable densities provide a deterministic fallback for permissive
    encodings such as latin-1, where arbitrary binary bytes are technically
    decodable. Raw NUL density is intentionally ignored for UTF-16/32 because
    zero bytes are normal in those encodings.
    """

    sample = bytes(payload[:CLASSIFIER_SAMPLE_BYTES])
    if not sample:
        return TextInspection(False, sample_bytes=0)

    decoded, decode_error = _strict_incremental_decode(sample, encoding)
    if decode_error is not None:
        return TextInspection(
            True,
            reason="the sample is not valid in the selected encoding",
            sample_bytes=len(sample),
            decode_error=decode_error,
        )

    length = max(1, len(decoded))
    controls = sum(
        1
        for char in decoded
        if (ord(char) < 0x20 and char not in "\t\n\r")
        or 0x7F <= ord(char) <= 0x9F
    )
    printable = sum(1 for char in decoded if char.isprintable() or char in "\t\n\r")
    nul_density = sample.count(b"\x00") / max(1, len(sample))
    control_density = controls / length
    printable_density = printable / length

    reasons = []
    if control_density > 0.05:
        reasons.append("the sample contains a high density of control characters")
    if printable_density < 0.70 and len(decoded) >= 32:
        reasons.append("the sample has a low printable-character density")
    if not _is_utf_family(encoding) and nul_density > 0.01:
        reasons.append("the sample contains a high density of NUL bytes")
    if sample.startswith(b"%PDF-") and reasons:
        reasons.insert(0, "the sample has a PDF binary signature")

    return TextInspection(
        bool(reasons),
        reason="; ".join(reasons),
        sample_bytes=len(sample),
        decoded_chars=len(decoded),
        printable_density=printable_density,
        control_density=control_density,
        nul_density=nul_density,
    )


def inspect_file(
    path: Path,
    encoding: str = "utf-8",
    *,
    sample_size: int = CLASSIFIER_SAMPLE_BYTES,
) -> Optional[TextInspection]:
    """Inspect a bounded raw-byte sample, returning ``None`` on read errors."""

    try:
        with path.open("rb") as handle:
            payload = handle.read(max(1, min(int(sample_size), CLASSIFIER_SAMPLE_BYTES)))
    except OSError:
        return None
    return inspect_bytes(payload, encoding)


def warning_for(path: Path, encoding: str, inspection: TextInspection) -> str:
    """Return the user-facing warning for a suspicious local source."""

    detail = inspection.reason or "the sample does not look like ordinary text"
    return (
        f"{path} does not appear to be readable text in the selected encoding "
        f"{encoding!r} ({detail}); terminal controls will be shown visibly."
    )


def _visible_control(char: str) -> str:
    codepoint = ord(char)
    picture = _CONTROL_PICTURES.get(codepoint)
    if picture is not None:
        return picture
    if 0x80 <= codepoint <= 0x9F:
        return f"\\x{codepoint:02x}"
    if _needs_unicode_escape(char):
        if codepoint <= 0xFFFF:
            return f"\\u{codepoint:04x}"
        return f"\\U{codepoint:08x}"
    return char


def _sanitize_display_char(char: str) -> str:
    if char == "\n":
        return "␊"
    if char == "\r":
        return "␍"
    if char == "\t":
        return char
    if char in {"\u200c", "\u200d"}:
        return char
    if char.isprintable() and not _needs_unicode_escape(char):
        return char
    return _visible_control(char)


def sanitize_source_text_with_boundaries(text: str) -> Tuple[str, Tuple[int, ...]]:
    """Return a safe display projection and raw-character boundaries.

    The returned boundary tuple has one entry for every boundary in ``text``.
    This lets display code translate canonical search spans without changing
    the raw text used by search and filtering.
    """

    parts: List[str] = []
    boundaries: List[int] = [0]
    visible_length = 0
    for char in text:
        safe = _sanitize_display_char(char)
        parts.append(safe)
        visible_length += len(safe)
        boundaries.append(visible_length)
    return "".join(parts), tuple(boundaries)


def sanitize_source_line(line: str) -> str:
    """Make one decoded source line safe to emit to a terminal.

    Newline delimiters remain structural. Every other C0/C1 control,
    including ESC, BEL, backspace, NUL, DEL, and carriage returns embedded in
    a line, becomes a visible deterministic representation.
    """

    terminator = ""
    body = line
    if body.endswith("\r\n"):
        body, terminator = body[:-2], "\r\n"
    elif body.endswith("\n") or body.endswith("\r"):
        body, terminator = body[:-1], body[-1:]

    safe, _ = sanitize_source_text_with_boundaries(body)
    return safe + terminator


def sanitize_source_text(text: str) -> str:
    """Sanitize arbitrary decoded text while retaining LF/CRLF structure."""

    out: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\n":
            out.append(sanitize_source_line(text[start:index] + "\n"))
            start = index + 1
        elif char == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            out.append(sanitize_source_line(text[start:index] + "\r\n"))
            start = index + 2
            index += 1
        index += 1
    if start < len(text):
        out.append(sanitize_source_line(text[start:]))
    return "".join(out)
