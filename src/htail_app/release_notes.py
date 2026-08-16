"""Release-note scoping for update UI and GitHub release bodies."""

from __future__ import annotations

import re
from typing import List, Tuple

from . import core


_ORIGINAL_RELEASE_NOTE_SECTIONS = core.release_note_sections
_NONE_ITEMS = {"none", "none.", "n/a", "n/a."}
_RELEASE_HEADING = re.compile(r"^#\s+htail\s+\S+\s*$", re.IGNORECASE)


def current_release_block(notes: str) -> str:
    """Return only the first top-level htail release block in a notes body."""
    lines = notes.splitlines()
    start = next((index for index, line in enumerate(lines) if _RELEASE_HEADING.match(line.strip())), None)
    if start is None:
        return notes
    stop = len(lines)
    for index in range(start + 1, len(lines)):
        if _RELEASE_HEADING.match(lines[index].strip()):
            stop = index
            break
    return "\n".join(lines[start:stop]).strip() + "\n"


def _real_items(items: List[str]) -> List[str]:
    return [item for item in items if item.strip().lower() not in _NONE_ITEMS]


def release_note_sections(notes: str) -> Tuple[List[str], List[str], List[str]]:
    features, fixes, other = _ORIGINAL_RELEASE_NOTE_SECTIONS(current_release_block(notes))
    return _real_items(features), _real_items(fixes), _real_items(other)


def install() -> None:
    if getattr(core, "_htail_release_note_extension", False):
        return
    core.release_note_sections = release_note_sections
    core._htail_release_note_extension = True
