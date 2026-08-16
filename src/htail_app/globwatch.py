from __future__ import annotations

from dataclasses import dataclass, field
import glob
import os
from pathlib import Path
from typing import Iterable, List, Set


def _norm(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def has_magic(value: str) -> bool:
    return glob.has_magic(value)


def glob_root(pattern: str) -> Path:
    """Return the deepest non-magic directory prefix for a glob pattern."""
    path = Path(pattern)
    fixed = []
    for part in path.parts:
        if glob.has_magic(part):
            break
        fixed.append(part)
    if not fixed:
        return Path.cwd()
    root = Path(*fixed)
    if not root.is_absolute():
        root = Path.cwd() / root
    # If the non-magic prefix names a file-like component, its parent is the
    # directory that can actually be watched. For the ordinary `logs/*.log`
    # case the prefix is already `logs` and remains unchanged.
    return _norm(root)


@dataclass
class DynamicGlob:
    pattern: str
    seen: Set[Path] = field(default_factory=set)

    @property
    def root(self) -> Path:
        return glob_root(self.pattern)

    def scan(self) -> List[Path]:
        matches: List[Path] = []
        for raw in glob.glob(self.pattern, recursive=True):
            path = Path(raw)
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            normalized = _norm(path)
            if normalized in self.seen:
                continue
            self.seen.add(normalized)
            matches.append(path)
        matches.sort(key=lambda p: os.fspath(p).lower())
        return matches

    def seed(self, paths: Iterable[Path]) -> None:
        for path in paths:
            self.seen.add(_norm(path))
