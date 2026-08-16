from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Iterable, List, Optional, Sequence, Tuple

from . import core
from .searching import (
    GlobalSearchMatch,
    SEARCH_FUZZY,
    compile_search,
)

SORT_FILE = "file"
SORT_RELEVANCE = "relevance"
FUZZY_SCORE_CUTOFF = 35.0

try:
    from rapidfuzz import fuzz as _rf_fuzz
    from rapidfuzz import process as _rf_process
    from rapidfuzz import __version__ as _rf_version
except Exception:  # source checkout without bundled dependency
    _rf_fuzz = None
    _rf_process = None
    _rf_version = None


@dataclass(frozen=True)
class CorpusLine:
    pane_index: int
    source_index: int
    pane_name: str
    text: str


@dataclass(frozen=True)
class SearchPage:
    results: List[GlobalSearchMatch]
    truncated: bool = False
    error: Optional[str] = None


def fuzzy_backend() -> str:
    if _rf_process is None:
        return "unavailable"
    return f"RapidFuzz {_rf_version} (C++)"


def build_corpus(panes: Sequence[object]) -> List[CorpusLine]:
    corpus: List[CorpusLine] = []
    for pane_index, pane in enumerate(panes):
        for source_index, raw in enumerate(pane.snapshot_raw):
            if not pane.display_filter.accepts(raw):
                continue
            corpus.append(CorpusLine(pane_index, source_index, pane.name, raw.rstrip("\r\n")))
    return corpus


def _fuzzy_span(query: str, text: str, ignore_case: bool) -> Tuple[int, int]:
    if not query or not text or _rf_fuzz is None:
        return 0, 0
    processor = str.casefold if ignore_case else None
    alignment = _rf_fuzz.partial_ratio_alignment(query, text, processor=processor)
    if alignment is None:
        return 0, min(1, len(text))
    return alignment.dest_start, min(len(text), alignment.dest_end)


def _sort_fuzzy_by_file(results: Sequence[GlobalSearchMatch]) -> List[GlobalSearchMatch]:
    grouped = defaultdict(list)
    pane_order: List[int] = []
    for result in results:
        if result.pane_index not in grouped:
            pane_order.append(result.pane_index)
        grouped[result.pane_index].append(result)
    pane_order.sort(
        key=lambda pane_index: max((r.score or 0.0) for r in grouped[pane_index]),
        reverse=True,
    )
    ordered: List[GlobalSearchMatch] = []
    for pane_index in pane_order:
        ordered.extend(sorted(grouped[pane_index], key=lambda result: -(result.score or 0.0)))
    return ordered


def search_corpus(
    corpus: Sequence[CorpusLine],
    expression: str,
    mode: str,
    flags: int,
    *,
    file_filter: Optional[int],
    sort_mode: str,
    limit: int,
) -> SearchPage:
    if not expression:
        return SearchPage([])
    candidates = [line for line in corpus if file_filter is None or line.pane_index == file_filter]
    if mode == SEARCH_FUZZY:
        if _rf_process is None or _rf_fuzz is None:
            return SearchPage([], error="RapidFuzz is unavailable in this runtime")
        if not candidates:
            return SearchPage([])
        ignore_case = bool(flags & re.IGNORECASE)
        processor = str.casefold if ignore_case else None
        choices = [line.text for line in candidates]
        extracted = _rf_process.extract(
            expression,
            choices,
            scorer=_rf_fuzz.partial_ratio,
            processor=processor,
            limit=max(1, limit + 1),
            score_cutoff=FUZZY_SCORE_CUTOFF,
        )
        truncated = len(extracted) > limit
        results: List[GlobalSearchMatch] = []
        for _choice, score, candidate_index in extracted[:limit]:
            line = candidates[candidate_index]
            start, end = _fuzzy_span(expression, line.text, ignore_case)
            results.append(
                GlobalSearchMatch(
                    line.pane_index,
                    line.source_index,
                    line.pane_name,
                    line.text,
                    start,
                    end,
                    float(score),
                )
            )
        if sort_mode == SORT_FILE:
            results = _sort_fuzzy_by_file(results)
        return SearchPage(results, truncated=truncated)

    pattern, error = compile_search(expression, mode, flags)
    if error is not None:
        return SearchPage([], error=error)
    if pattern is None:
        return SearchPage([])
    results: List[GlobalSearchMatch] = []
    truncated = False
    for line in candidates:
        match = pattern.search(line.text)
        if match is None:
            continue
        results.append(
            GlobalSearchMatch(
                line.pane_index,
                line.source_index,
                line.pane_name,
                line.text,
                match.start(),
                match.end(),
                None,
            )
        )
        if len(results) > limit:
            truncated = True
            results = results[:limit]
            break
    return SearchPage(results, truncated=truncated)


def _pad(text: str, width: int) -> str:
    text = core.clip_ansi(text, max(0, width))
    visible = len(core.strip_ansi(text))
    return text + " " * max(0, width - visible)


def _tab(label: str, active: bool, color: bool) -> str:
    plain = f"[{label}]" if active else label
    if not color:
        return plain
    if active:
        return core.paint(plain, "\x1b[1;30;106m", True)
    return core.paint(plain, core.DIM, True)


def _highlight_span(text: str, start: int, end: int, *, selected: bool, color: bool) -> str:
    if not color or end <= start or start < 0 or start >= len(text):
        return text
    end = min(len(text), end)
    on = "\x1b[1;30;48;5;208m" if selected else "\x1b[1;30;106m"
    off = "\x1b[1;97;48;5;24m" if selected else "\x1b[0m"
    return text[:start] + on + text[start:end] + off + text[end:]


def _flat_column_widths(width: int) -> Tuple[int, int, int, int]:
    filename_width = max(12, min(28, width // 4))
    line_width = 6
    score_width = 5
    fixed = 2 + 3 + 2 + filename_width + 1 + line_width + 2 + 2 + score_width
    preview_width = max(8, width - fixed)
    return filename_width, line_width, preview_width, score_width


def _flat_result_header(width: int) -> str:
    filename_width, line_width, preview_width, score_width = _flat_column_widths(width)
    row = (
        f"  {'#':>3}  {'FILE':<{filename_width}.{filename_width}} "
        f"{'LINE':>{line_width}}  {'MATCH':<{preview_width}.{preview_width}}  {'SCORE':>{score_width}}"
    )
    return _pad(row, width)


def _flat_result_rows(
    results: Sequence[GlobalSearchMatch], selected: int, rows: int, width: int, color: bool
) -> Tuple[List[str], List[Tuple[str, int]]]:
    if not results:
        return [], []
    start = max(0, selected - rows // 2)
    start = min(start, max(0, len(results) - rows))
    out: List[str] = []
    tags: List[Tuple[str, int]] = []
    filename_width, line_width, preview_width, score_width = _flat_column_widths(width)
    for index in range(start, min(len(results), start + rows)):
        result = results[index]
        selected_row = index == selected
        preview = result.text
        if len(preview) > preview_width:
            left = max(0, min(result.match_start - preview_width // 3, len(preview) - preview_width))
            right = min(len(preview), left + preview_width)
            local_start = max(0, result.match_start - left)
            local_end = max(local_start, min(right - left, result.match_end - left))
            preview = preview[left:right]
            if left > 0 and preview:
                preview = "…" + preview[1:]
            if right < len(result.text) and preview:
                preview = preview[:-1] + "…"
        else:
            local_start, local_end = result.match_start, result.match_end
        preview = _highlight_span(preview, local_start, local_end, selected=selected_row, color=color)
        score = f"{result.score:>{score_width}.0f}" if result.score is not None else " " * score_width
        marker = "▌" if selected_row else " "
        row = (
            f"{marker} {index + 1:>3}  {result.pane_name:<{filename_width}.{filename_width}} "
            f"{result.source_index + 1:>{line_width}}  {_pad(preview, preview_width)}  {score}"
        )
        if selected_row and color:
            row = "\x1b[1;97;48;5;24m" + row + core.RESET
        out.append(_pad(row, width))
        tags.append(("result", index))
    return out, tags


def _grouped_result_rows(
    results: Sequence[GlobalSearchMatch],
    selected: int,
    rows: int,
    width: int,
    color: bool,
    expanded_pane: Optional[int],
) -> Tuple[List[str], List[Tuple[str, int]]]:
    if not results:
        return [], []
    groups: List[Tuple[int, str, List[Tuple[int, GlobalSearchMatch]]]] = []
    group_map = {}
    for index, result in enumerate(results):
        if result.pane_index not in group_map:
            group_map[result.pane_index] = len(groups)
            groups.append((result.pane_index, result.pane_name, []))
        groups[group_map[result.pane_index]][2].append((index, result))
    selected_pane = results[selected].pane_index
    header_count = min(len(groups), rows)
    active_slots = max(1, rows - header_count)
    out: List[str] = []
    tags: List[Tuple[str, int]] = []
    for pane_index, pane_name, members in groups:
        expanded = pane_index == expanded_pane
        selected_group = pane_index == selected_pane
        symbol = "▼" if expanded else "▶"
        best = max((member.score or 0.0) for _, member in members)
        score_suffix = f" · best {best:.0f}" if members and members[0][1].score is not None else ""
        header = f"{symbol} {pane_name}  {len(members)}{score_suffix}"
        style = core.BOLD_LIGHT_CYAN if selected_group else core.DIM
        out.append(_pad(core.paint(header, style, color), width))
        tags.append(("file", pane_index))
        if len(out) >= rows:
            break
        if not expanded:
            continue
        selected_member_pos = next((i for i, (global_index, _) in enumerate(members) if global_index == selected), 0)
        member_start = max(0, selected_member_pos - active_slots // 2)
        member_start = min(member_start, max(0, len(members) - active_slots))
        for global_index, result in members[member_start:member_start + active_slots]:
            selected_row = global_index == selected
            marker = "▌" if selected_row else " "
            prefix = f"{marker} {result.source_index + 1:>6}  "
            room = max(8, width - len(prefix) - 8)
            preview = result.text
            local_start, local_end = result.match_start, result.match_end
            if len(preview) > room:
                left = max(0, min(result.match_start - room // 3, len(preview) - room))
                right = min(len(preview), left + room)
                local_start = max(0, result.match_start - left)
                local_end = max(local_start, min(right - left, result.match_end - left))
                preview = preview[left:right]
                if left > 0 and preview:
                    preview = "…" + preview[1:]
                if right < len(result.text) and preview:
                    preview = preview[:-1] + "…"
            preview = _highlight_span(preview, local_start, local_end, selected=selected_row, color=color)
            score = f"  {result.score:.0f}" if result.score is not None else ""
            row = prefix + preview + score
            if selected_row and color:
                row = "\x1b[1;97;48;5;24m" + row + core.RESET
            out.append(_pad(row, width))
            tags.append(("result", global_index))
            if len(out) >= rows:
                break
        if len(out) >= rows:
            break
    return out[:rows], tags[:rows]


def _preview_rows(
    panes: Sequence[object], result: Optional[GlobalSearchMatch], rows: int, width: int, color: bool
) -> List[str]:
    if result is None or result.pane_index >= len(panes):
        return [core.paint("Select a result to preview its context.", core.DIM, color)]
    pane = panes[result.pane_index]
    lines = pane.snapshot_raw
    if not lines:
        return []
    context_rows = max(1, rows - 2)
    start = max(0, result.source_index - context_rows // 2)
    start = min(start, max(0, len(lines) - context_rows))
    out = [core.paint(f"{result.pane_name}:{result.source_index + 1}", core.BOLD_LIGHT_CYAN, color), ""]
    number_width = len(str(min(len(lines), start + context_rows)))
    for source_index in range(start, min(len(lines), start + context_rows)):
        raw_text = lines[source_index].rstrip("\r\n")
        text = raw_text.replace("\t", "    ")
        selected = source_index == result.source_index
        marker = ">" if selected else " "
        prefix = f"{marker} {source_index + 1:>{number_width}} │ "
        room = max(1, width - len(prefix))
        if selected:
            match_start = len(raw_text[:result.match_start].replace("\t", "    "))
            match_end = len(raw_text[:result.match_end].replace("\t", "    "))
            local_start, local_end = match_start, match_end
            if len(text) > room:
                left = max(0, min(match_start - room // 3, len(text) - room))
                right = min(len(text), left + room)
                local_start = max(0, match_start - left)
                local_end = max(local_start, min(right - left, match_end - left))
                text = text[left:right]
                if left > 0 and text:
                    text = "…" + text[1:]
                if right < len(raw_text.replace("\t", "    ")) and text:
                    text = text[:-1] + "…"
            text = _highlight_span(text, local_start, local_end, selected=True, color=color)
        row = prefix + text
        if selected and color:
            row = "\x1b[1;97;48;5;24m" + row + core.RESET
        out.append(_pad(row, width))
    return out[:rows]


def render_global_search(
    width: int,
    height: int,
    *,
    query: str,
    mode: str,
    mode_labels: Sequence[Tuple[str, str]],
    ignore_case: bool,
    sort_mode: str,
    file_filter_label: str,
    results: Sequence[GlobalSearchMatch],
    selected: int,
    truncated: bool,
    error: Optional[str],
    panes: Sequence[object],
    preview_enabled: bool,
    color: bool,
    expanded_pane: Optional[int] = None,
    hit_regions: Optional[List[Tuple[int, int, int, int, str, int]]] = None,
) -> List[str]:
    if width < 40 or height < 10:
        return [" " * width for _ in range(height)]
    panel_width = min(width - 2, max(72, int(width * 0.92)))
    panel_height = min(height, max(12, int(height * 0.88)))
    panel_width = min(panel_width, width)
    panel_height = min(panel_height, height)
    inner = panel_width - 2
    left_margin = max(0, (width - panel_width) // 2)
    top_margin = max(0, (height - panel_height) // 2)

    files_with_hits = len({result.pane_index for result in results})
    count = len(results)
    count_label = f"{count}+ matches" if truncated else f"{count} match{'es' if count != 1 else ''}"
    if files_with_hits:
        count_label += f" · {files_with_hits} file{'s' if files_with_hits != 1 else ''}"

    mode_parts = [_tab(label, mode == value, color) for value, label in mode_labels]
    case_label = _tab("NoCase" if ignore_case else "Case", True, color)
    sort_relevance = _tab("Relevance", sort_mode == SORT_RELEVANCE, color)
    sort_file = _tab("File", sort_mode == SORT_FILE, color)
    mode_row = "  ".join(mode_parts) + f"    Case: {case_label}    Sort: {sort_relevance}  {sort_file}"
    if len(core.strip_ansi(mode_row)) + len(count_label) + 2 <= inner:
        mode_row += " " * (inner - len(core.strip_ansi(mode_row)) - len(count_label)) + count_label

    search_row = core.paint("Search: ", core.DIM, color) + query + core.paint("▌", core.BOLD_LIGHT_CYAN, color)
    filter_row = f"Files: {file_filter_label}"
    if mode == SEARCH_FUZZY:
        filter_row += "    " + core.paint(f"Backend: {fuzzy_backend()}", core.DIM, color)

    header_rows = [search_row, mode_row, filter_row]
    footer_text = "↑↓ match · Shift+↑↓ file · Enter jump · Tab mode · Ctrl+T case · Ctrl+O(letter) sort · Ctrl+F file · Ctrl+P preview · Esc close"

    show_preview = preview_enabled and panel_width >= 96
    body_height = max(3, panel_height - 8)
    if show_preview:
        left_width = max(40, int((inner - 1) * 0.62))
        right_width = inner - left_width - 1
    else:
        left_width = inner
        right_width = 0

    selected_result = results[selected] if results and 0 <= selected < len(results) else None
    left_tags: List[Optional[Tuple[str, int]]] = []
    if error:
        left_rows = [core.paint(f"Invalid search: {error}", core.BOLD_YELLOW, color)]
        left_tags = [None]
    elif not query:
        left_rows = [core.paint("Type to search every currently watched file.", core.DIM, color)]
        left_tags = [None]
    elif not results:
        left_rows = [core.paint("No matches.", core.DIM, color)]
        left_tags = [None]
    elif sort_mode == SORT_RELEVANCE:
        result_rows, result_tags = _flat_result_rows(results, selected, max(1, body_height - 2), left_width, color)
        left_rows = [core.paint(_flat_result_header(left_width), core.DIM, color)] + result_rows
        left_tags = [None] + result_tags
    else:
        left_rows, grouped_tags = _grouped_result_rows(
            results, selected, max(1, body_height - 1), left_width, color, expanded_pane
        )
        left_tags = list(grouped_tags)
    left_heading = "RESULTS — best matches" if sort_mode == SORT_RELEVANCE else "RESULTS — grouped by file"
    left_rows = [core.paint(left_heading, core.BOLD_LIGHT_CYAN, color)] + left_rows
    left_tags = [None] + left_tags
    left_rows = left_rows[:body_height] + [""] * max(0, body_height - len(left_rows))
    left_tags = left_tags[:body_height] + [None] * max(0, body_height - len(left_tags))

    if hit_regions is not None:
        hit_regions.clear()
        body_y = top_margin + 5
        content_x1 = left_margin + 1
        content_x2 = content_x1 + left_width
        for offset, tag in enumerate(left_tags):
            if tag is None:
                continue
            kind, value = tag
            hit_regions.append((content_x1, body_y + offset, content_x2, body_y + offset + 1, kind, value))

    if show_preview:
        right_rows = [core.paint("PREVIEW", core.BOLD_LIGHT_CYAN, color)]
        right_rows.extend(_preview_rows(panes, selected_result, body_height - 1, right_width, color))
        right_rows = right_rows[:body_height] + [""] * max(0, body_height - len(right_rows))

    title = " Global search "
    title_fill = max(0, panel_width - 2 - len(title))
    top = "╭" + "─" * min(3, title_fill) + title + "─" * max(0, title_fill - 3) + "╮"
    bottom = "╰" + "─" * (panel_width - 2) + "╯"
    panel: List[str] = [core.paint(top, core.BOLD_LIGHT_CYAN, color)]
    for row in header_rows:
        panel.append(core.paint("│", core.CYAN, color) + _pad(" " + row, inner) + core.paint("│", core.CYAN, color))
    if show_preview:
        divider = "├" + "─" * left_width + "┬" + "─" * right_width + "┤"
    else:
        divider = "├" + "─" * inner + "┤"
    panel.append(core.paint(divider, core.CYAN, color))
    for row_index in range(body_height):
        if show_preview:
            panel.append(
                core.paint("│", core.CYAN, color)
                + _pad(left_rows[row_index], left_width)
                + core.paint("│", core.CYAN, color)
                + _pad(right_rows[row_index], right_width)
                + core.paint("│", core.CYAN, color)
            )
        else:
            panel.append(core.paint("│", core.CYAN, color) + _pad(left_rows[row_index], inner) + core.paint("│", core.CYAN, color))
    footer_divider = "├" + "─" * (panel_width - 2) + "┤"
    panel.append(core.paint(footer_divider, core.CYAN, color))
    panel.append(core.paint("│", core.CYAN, color) + _pad(" " + footer_text, inner) + core.paint("│", core.CYAN, color))
    panel.append(core.paint(bottom, core.BOLD_LIGHT_CYAN, color))
    panel = panel[:panel_height]

    out = [" " * width for _ in range(height)]
    for index, row in enumerate(panel):
        y = top_margin + index
        if y >= height:
            break
        left = " " * left_margin
        right = " " * max(0, width - left_margin - len(core.strip_ansi(row)))
        out[y] = left + row + right
    if not color:
        out = [core.strip_ansi(row) for row in out]
    return out