from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from . import core


_BaseSyntaxHighlighter = core.SyntaxHighlighter


class MarkdownTableHighlighter(_BaseSyntaxHighlighter):
    """Application highlighter with aligned Markdown pipe-table rendering."""

    @staticmethod
    def _split_markdown_table_row(body: str) -> Optional[List[str]]:
        stripped = body.strip()
        if "|" not in stripped:
            return None
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|") and not stripped.endswith(r"\|"):
            stripped = stripped[:-1]

        cells: List[str] = []
        current: List[str] = []
        escaped = False
        for char in stripped:
            if escaped:
                current.append(char)
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == "|":
                cells.append("".join(current).strip())
                current = []
                continue
            current.append(char)
        if escaped:
            current.append("\\")
        cells.append("".join(current).strip())
        return cells

    @classmethod
    def _markdown_table_alignments(cls, body: str) -> Optional[List[str]]:
        cells = cls._split_markdown_table_row(body)
        if not cells:
            return None
        alignments: List[str] = []
        for cell in cells:
            marker = cell.replace(" ", "")
            if re.fullmatch(r":?-{3,}:?", marker) is None:
                return None
            if marker.startswith(":") and marker.endswith(":"):
                alignments.append("center")
            elif marker.endswith(":"):
                alignments.append("right")
            else:
                alignments.append("left")
        return alignments

    @classmethod
    def _contains_markdown_table(cls, lines: Sequence[str]) -> bool:
        for index in range(1, len(lines)):
            if cls._markdown_table_alignments(lines[index].rstrip("\r\n")) is None:
                continue
            if cls._split_markdown_table_row(lines[index - 1].rstrip("\r\n")) is not None:
                return True
        return False

    @staticmethod
    def _pad_markdown_table_cell(text: str, width: int, alignment: str) -> str:
        visible = len(core.strip_ansi(text))
        gap = max(0, width - visible)
        if alignment == "right":
            return (" " * gap) + text
        if alignment == "center":
            left = gap // 2
            return (" " * left) + text + (" " * (gap - left))
        return text + (" " * gap)

    def _render_markdown_table_block(
        self,
        lines: Sequence[str],
        start: int,
    ) -> Optional[Tuple[List[str], int]]:
        if start + 1 >= len(lines):
            return None
        header = self._split_markdown_table_row(lines[start].rstrip("\r\n"))
        alignments = self._markdown_table_alignments(lines[start + 1].rstrip("\r\n"))
        if header is None or alignments is None:
            return None

        column_count = len(alignments)
        if column_count == 0:
            return None

        raw_rows: List[List[str]] = [header[:column_count]]
        index = start + 2
        while index < len(lines):
            body = lines[index].rstrip("\r\n")
            if not body.strip():
                break
            cells = self._split_markdown_table_row(body)
            if cells is None:
                break
            raw_rows.append(cells[:column_count])
            index += 1

        for cells in raw_rows:
            cells.extend([""] * (column_count - len(cells)))

        styled_rows = [[self._inline_md(cell) for cell in cells] for cells in raw_rows]
        widths = [
            max(len(core.strip_ansi(row[column])) for row in styled_rows)
            for column in range(column_count)
        ]

        rendered: List[str] = []
        for row_index, cells in enumerate(styled_rows):
            padded = [
                self._pad_markdown_table_cell(cells[column], widths[column], alignments[column])
                for column in range(column_count)
            ]
            if row_index == 0 and self.color:
                padded = [core.paint(cell, core.BOLD_LIGHT_CYAN, True) for cell in padded]
            rendered.append("│ " + " │ ".join(padded) + " │")

        separator = "├" + "┼".join("─" * (width + 2) for width in widths) + "┤"
        rendered.insert(1, core.paint(separator, core.DIM, self.color))
        consumed = 2 + max(0, len(raw_rows) - 1)
        return rendered, consumed

    def _render_markdown_lines(self, lines: Sequence[str]) -> List[str]:
        rendered: List[str] = []
        in_fence = False
        fence_marker = ""
        fence_lexer = None
        code_formatter = core.Terminal256Formatter() if core.HAVE_PYGMENTS else None

        index = 0
        while index < len(lines):
            raw = lines[index]
            body = raw.rstrip("\r\n")
            fence = re.match(r"^(\s*)(```|~~~)\s*([^\s`]*)?.*$", body)

            if fence:
                marker = fence.group(2)
                language = (fence.group(3) or "").strip()
                if not in_fence:
                    in_fence = True
                    fence_marker = marker
                    fence_lexer = None
                    if language and core.HAVE_PYGMENTS:
                        try:
                            fence_lexer = core.get_lexer_by_name(language)
                        except core.ClassNotFound:
                            fence_lexer = None
                    label = f" {language} " if language else ""
                    rendered.append(
                        core.paint("┌─" + label + "─" * 4, core.DIM, self.color)
                    )
                elif marker.startswith(fence_marker[0]):
                    in_fence = False
                    fence_marker = ""
                    fence_lexer = None
                    rendered.append(core.paint("└────", core.DIM, self.color))
                else:
                    rendered.append(body)
                index += 1
                continue

            if in_fence:
                code = body
                if fence_lexer is not None and code_formatter is not None:
                    styled = core.highlight(code + "\n", fence_lexer, code_formatter).rstrip("\r\n")
                else:
                    styled = core.paint(code, core.MAGENTA, self.color)
                rendered.append("  " + styled)
                index += 1
                continue

            table = self._render_markdown_table_block(lines, index)
            if table is not None:
                table_rows, consumed = table
                rendered.extend(table_rows)
                index += consumed
                continue

            rendered.append(self._render_markdown_line(body))
            index += 1

        return rendered


def install() -> None:
    """Install app-layer rendering additions without changing the frozen core file."""
    core.BOLD_LIGHT_MAGENTA = "\x1b[1;95m"
    core.SyntaxHighlighter = MarkdownTableHighlighter
