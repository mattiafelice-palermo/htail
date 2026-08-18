from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Iterable, List, Optional, Sequence, Tuple

from . import core
from . import terminal_cells
from .searching import (
    GlobalSearchMatch,
    SEARCH_FUZZY,
    compile_search,
)
from .text_safety import sanitize_source_text_with_boundaries

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


@dataclass(frozen=True)
class _DisplayProjection:
    text: str
    raw_boundaries: Tuple[int, ...]

    def span(self, start: int, end: int) -> Tuple[int, int]:
        start = max(0, min(int(start), len(self.raw_boundaries) - 1))
        end = max(start, min(int(end), len(self.raw_boundaries) - 1))
        return self.raw_boundaries[start], self.raw_boundaries[end]


def _source_projection(text: str) -> _DisplayProjection:
    safe, safe_boundaries = sanitize_source_text_with_boundaries(text)
    expanded, expanded_boundaries = terminal_cells.expand_tabs_ansi_with_boundaries(
        safe, tuple(range(len(safe) + 1))
    )
    return _DisplayProjection(
        expanded,
        tuple(expanded_boundaries[offset] for offset in safe_boundaries),
    )


def _display_name(text: str) -> str:
    return _source_projection(text).text


def _crop_display_projection(
    projection: _DisplayProjection,
    match_start: int,
    match_end: int,
    width: int,
) -> Tuple[str, int, int]:
    """Crop a projected source line to cells while retaining a match span."""

    if width <= 0:
        return "", 0, 0
    text = projection.text
    if terminal_cells.display_width(text) <= width:
        return text, match_start, match_end

    boundaries = terminal_cells.cell_boundaries(text)
    total = boundaries[-1]
    anchor = boundaries[max(0, min(match_start, len(text)))]
    left_cell = max(0, min(anchor - width // 3, total - width))
    return _slice_display_span(text, left_cell, width, match_start, match_end)


def _slice_display_span(
    text: str,
    left_cell: int,
    width: int,
    match_start: int,
    match_end: int,
) -> Tuple[str, int, int]:
    first, last = terminal_cells.cell_slice_bounds(text, left_cell, width)
    if last <= first:
        return "", 0, 0
    chunk = text[first:last]
    local_start = max(0, match_start - first)
    local_end = max(local_start, min(last - first, match_end - first))

    hidden_left = first > 0
    hidden_right = last < len(text)
    if hidden_left and chunk:
        removed_start, removed_end = terminal_cells.cell_unit_bounds(chunk)
        removed = removed_end - removed_start
        old_start, old_end = local_start, local_end
        chunk = "…" + chunk[removed_end:]
        if old_start < removed:
            local_start, local_end = 0, max(1, old_end)
        else:
            local_start = 1 + old_start - removed
            local_end = 1 + max(old_start, old_end) - removed
    if hidden_right and chunk:
        removed_start, removed_end = terminal_cells.cell_unit_bounds(chunk, from_end=True)
        if local_end > removed_start:
            local_end = max(local_start, removed_start)
        chunk = chunk[:removed_start] + "…"
    return chunk, local_start, local_end


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
    text = terminal_cells.clip_cells_ansi(text, max(0, width))
    visible = terminal_cells.display_width(text)
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
        projection = _source_projection(result.text)
        display_start, display_end = projection.span(result.match_start, result.match_end)
        preview, local_start, local_end = _crop_display_projection(
            projection, display_start, display_end, preview_width
        )
        preview = _highlight_span(preview, local_start, local_end, selected=selected_row, color=color)
        score = f"{result.score:>{score_width}.0f}" if result.score is not None else " " * score_width
        marker = "▌" if selected_row else " "
        row = (
            f"{marker} {index + 1:>3}  {_pad(_display_name(result.pane_name), filename_width)} "
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
        header = f"{symbol} {_display_name(pane_name)}  {len(members)}{score_suffix}"
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
            score = f"  {result.score:.0f}" if result.score is not None else ""
            room = max(8, width - terminal_cells.display_width(prefix) - terminal_cells.display_width(score))
            projection = _source_projection(result.text)
            display_start, display_end = projection.span(result.match_start, result.match_end)
            preview, local_start, local_end = _crop_display_projection(
                projection, display_start, display_end, room
            )
            preview = _highlight_span(preview, local_start, local_end, selected=selected_row, color=color)
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


def _expanded_match_span(raw_text: str, result: GlobalSearchMatch) -> Tuple[int, int]:
    return _source_projection(raw_text).span(result.match_start, result.match_end)


def preview_text_width(width: int, height: int, line_count: int) -> int:
    """Return the usable source-text width of the global-search preview pane."""
    if width < 40 or height < 10:
        return 0
    panel_width = min(width - 2, max(72, int(width * 0.92)))
    panel_width = min(panel_width, width)
    if panel_width < 96:
        return 0
    inner = panel_width - 2
    left_width = max(40, int((inner - 1) * 0.62))
    right_width = inner - left_width - 1
    number_width = len(str(max(1, line_count)))
    prefix_width = number_width + 5
    return max(1, right_width - prefix_width)


def _preview_rows(
    panes: Sequence[object],
    result: Optional[GlobalSearchMatch],
    rows: int,
    width: int,
    color: bool,
    *,
    wrap: bool = False,
    scroll: int = 0,
    hscroll: int = 0,
) -> List[str]:
    if result is None or result.pane_index >= len(panes):
        return [core.paint("Select a result to preview its context.", core.DIM, color)]
    pane = panes[result.pane_index]
    lines = pane.snapshot_raw
    if not lines:
        return []

    context_rows = max(1, rows - 2)
    selected_index = min(max(0, result.source_index), len(lines) - 1)
    anchor_index = min(max(0, selected_index + scroll), len(lines) - 1)
    source_start = max(0, anchor_index - context_rows)
    source_end = min(len(lines), anchor_index + context_rows + 1)
    number_width = len(str(max(1, len(lines))))
    prefix_width = number_width + 5
    room = max(1, width - prefix_width)

    selected_raw = lines[selected_index].rstrip("\r\n")
    selected_projection = _source_projection(selected_raw)
    selected_text = selected_projection.text
    match_start, match_end = selected_projection.span(result.match_start, result.match_end)
    selected_boundaries = terminal_cells.cell_boundaries(selected_text)
    selected_total = selected_boundaries[-1]
    match_anchor = selected_boundaries[max(0, min(match_start, len(selected_text)))]
    base_left = max(0, min(match_anchor - room // 3, max(0, selected_total - room)))
    view_left = max(0, min(base_left + hscroll, max(0, selected_total - room)))

    visual_rows: List[str] = []
    focus_index: Optional[int] = None
    for source_index in range(source_start, source_end):
        raw_text = lines[source_index].rstrip("\r\n")
        projection = _source_projection(raw_text)
        text = projection.text
        selected = source_index == selected_index
        first_marker = ">" if selected else " "
        first_prefix = f"{first_marker} {source_index + 1:>{number_width}} │ "
        continuation_prefix = f"  {'':>{number_width}} │ "

        if selected:
            local_match_start, local_match_end = projection.span(result.match_start, result.match_end)
        else:
            local_match_start = local_match_end = 0

        if wrap:
            chunk_bounds = terminal_cells.cell_chunks(text, room)
            focus_segment = 0
            if source_index == anchor_index and source_index == selected_index and scroll == 0:
                focus_segment = next(
                    (
                        index
                        for index, (chunk_start, chunk_end) in enumerate(chunk_bounds)
                        if chunk_start <= local_match_start < chunk_end
                    ),
                    max(0, len(chunk_bounds) - 1),
                )
            for segment_index, (chunk_start, chunk_end) in enumerate(chunk_bounds):
                chunk = text[chunk_start:chunk_end]
                if selected:
                    highlight_start = max(0, local_match_start - chunk_start)
                    highlight_end = min(len(chunk), local_match_end - chunk_start)
                    if highlight_end > highlight_start:
                        chunk = _highlight_span(
                            chunk,
                            highlight_start,
                            highlight_end,
                            selected=True,
                            color=color,
                        )
                if source_index == anchor_index and segment_index == focus_segment:
                    focus_index = len(visual_rows)
                prefix = first_prefix if segment_index == 0 else continuation_prefix
                row = prefix + chunk
                if selected and color:
                    row = "\x1b[1;97;48;5;24m" + row + core.RESET
                visual_rows.append(_pad(row, width))
        else:
            chunk, highlight_start, highlight_end = _slice_display_span(
                text, view_left, room, local_match_start, local_match_end
            )
            if selected and highlight_end > highlight_start:
                chunk = _highlight_span(
                    chunk,
                    highlight_start,
                    highlight_end,
                    selected=True,
                    color=color,
                )
            if source_index == anchor_index:
                focus_index = len(visual_rows)
            row = first_prefix + chunk
            if selected and color:
                row = "\x1b[1;97;48;5;24m" + row + core.RESET
            visual_rows.append(_pad(row, width))

    if focus_index is None:
        focus_index = 0
    top = max(0, focus_index - context_rows // 2)
    top = min(top, max(0, len(visual_rows) - context_rows))
    visible = visual_rows[top:top + context_rows]
    out = [core.paint(f"{_display_name(result.pane_name)}:{result.source_index + 1}", core.BOLD_LIGHT_CYAN, color), ""]
    out.extend(visible)
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
    preview_wrap: bool = True,
    preview_scroll: int = 0,
    preview_hscroll: int = 0,
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
    mode_width = terminal_cells.display_width(mode_row)
    count_width = terminal_cells.display_width(count_label)
    if mode_width + count_width + 2 <= inner:
        mode_row += " " * (inner - mode_width - count_width) + count_label

    search_row = core.paint("Search: ", core.DIM, color) + query + core.paint("▌", core.BOLD_LIGHT_CYAN, color)
    filter_row = f"Files: {_display_name(file_filter_label)}"
    if mode == SEARCH_FUZZY:
        filter_row += "    " + core.paint(f"Backend: {fuzzy_backend()}", core.DIM, color)

    header_rows = [search_row, mode_row, filter_row]

    show_preview = preview_enabled and panel_width >= 96
    if show_preview:
        footer_text = "↑↓ match · Shift+↑↓ file · Ctrl+↑↓/Pg preview · Ctrl+W wrap · ←→ hscroll · Enter jump · Ctrl+O(letter) sort · Ctrl+P preview · Esc close"
    else:
        footer_text = "↑↓ match · Shift+↑↓ file · Enter jump · Tab mode · Ctrl+T case · Ctrl+O(letter) sort · Ctrl+F file · Ctrl+P preview · Esc close"
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
            preview_x1 = left_margin + left_width + 2
            hit_regions.append(
                (preview_x1, body_y, preview_x1 + right_width, body_y + body_height, "preview", 0)
            )

    if show_preview:
        preview_state = "WRAP" if preview_wrap else "NOWRAP"
        if preview_scroll:
            preview_state += f" · ctx {preview_scroll:+d}"
        if not preview_wrap and preview_hscroll:
            preview_state += f" · x {preview_hscroll:+d}"
        right_rows = [core.paint(f"PREVIEW · {preview_state}", core.BOLD_LIGHT_CYAN, color)]
        right_rows.extend(
            _preview_rows(
                panes,
                selected_result,
                body_height - 1,
                right_width,
                color,
                wrap=preview_wrap,
                scroll=preview_scroll,
                hscroll=preview_hscroll,
            )
        )
        right_rows = right_rows[:body_height] + [""] * max(0, body_height - len(right_rows))

    title = " Global search "
    title_fill = max(0, panel_width - 2 - terminal_cells.display_width(title))
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
        right = " " * max(0, width - left_margin - terminal_cells.display_width(row))
        out[y] = left + row + right
    if not color:
        out = [core.strip_ansi(row) for row in out]
    return out
