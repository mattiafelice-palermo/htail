from __future__ import annotations

from dataclasses import dataclass
import math
from typing import List, Optional, Sequence

LAYOUTS = ("auto", "rows", "columns", "grid", "stream")


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def _split(total: int, count: int) -> List[int]:
    if count <= 0:
        return []
    base, extra = divmod(max(0, total), count)
    return [base + (1 if i < extra else 0) for i in range(count)]


def _weighted_split(total: int, count: int, weights: Optional[Sequence[float]]) -> List[int]:
    if count <= 0:
        return []
    if weights is None or len(weights) != count:
        return _split(total, count)
    clean = [max(0.001, float(weight)) for weight in weights]
    if total < count:
        return _split(total, count)
    remaining = total - count
    weight_total = sum(clean)
    raw = [(remaining * weight / weight_total) for weight in clean]
    extras = [int(value) for value in raw]
    left = remaining - sum(extras)
    order = sorted(range(count), key=lambda index: raw[index] - extras[index], reverse=True)
    for index in order[:left]:
        extras[index] += 1
    return [1 + extra for extra in extras]


def resolve_auto(count: int, width: int, height: int) -> str:
    if count <= 1:
        return "rows"
    if count == 2:
        # Prefer side-by-side only when each pane retains a useful reading width.
        return "columns" if width >= 100 and width >= max(2 * 42, height * 2) else "rows"
    return "grid"


def pane_rects(
    layout: str,
    count: int,
    width: int,
    height: int,
    weights: Optional[Sequence[float]] = None,
) -> List[Rect]:
    """Return non-overlapping pane rectangles covering the available body area."""
    if count <= 0 or width <= 0 or height <= 0:
        return []
    if layout == "auto":
        layout = resolve_auto(count, width, height)
    if layout == "stream":
        return [Rect(0, 0, width, height)]

    if layout == "rows":
        heights = _weighted_split(height, count, weights)
        y = 0
        out: List[Rect] = []
        for h in heights:
            out.append(Rect(0, y, width, h))
            y += h
        return out

    if layout == "columns":
        widths = _weighted_split(width, count, weights)
        x = 0
        out = []
        for w in widths:
            out.append(Rect(x, 0, w, height))
            x += w
        return out

    if layout != "grid":
        raise ValueError(f"unknown layout: {layout}")

    # Bias the grid using terminal aspect ratio. Terminal cells are usually
    # taller than wide, so 2:1 is a reasonable visual correction.
    aspect = max(0.25, width / max(1.0, height * 2.0))
    columns = max(1, min(count, math.ceil(math.sqrt(count * aspect))))
    rows = math.ceil(count / columns)
    col_widths = _split(width, columns)
    row_heights = _split(height, rows)

    out = []
    index = 0
    y = 0
    for row in range(rows):
        x = 0
        for col in range(columns):
            if index >= count:
                break
            out.append(Rect(x, y, col_widths[col], row_heights[row]))
            x += col_widths[col]
            index += 1
        y += row_heights[row]
    return out
