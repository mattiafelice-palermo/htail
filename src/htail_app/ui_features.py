"""Interactive pane chrome: UI palettes, scrollbars, pane closing and selection."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
import re
import sys
import time
from typing import Dict, List, Mapping, Optional, Tuple

from . import core
from . import input as input_module
from . import pane as pane_module


DOUBLE_CLICK_SECONDS = 0.42
UI_PALETTE_STATE_KEY = "ui_palette"
UI_PALETTES_STATE_KEY = "ui_palettes"
SCROLLBAR_STYLE_STATE_KEY = "scrollbar_style"
SCROLLBAR_STYLES = ("rail", "border", "minimal", "off")
SCROLLBAR_LABELS = {
    "rail": "Rail",
    "border": "Border",
    "minimal": "Minimal",
    "off": "Off",
}


@dataclass(frozen=True)
class MouseSelection:
    mode: str
    start_visual: int
    start_col: int
    end_visual: int
    end_col: int
    text: str

# Values are ANSI 256-colour indexes. Built-ins are immutable in the editor.
BUILTIN_PALETTES: Dict[str, Dict[str, int]] = {
    "default": {
        "accent": 51,
        "muted": 244,
        "warning": 226,
        "error": 203,
        "success": 46,
        "secondary": 201,
        "selection_fg": 16,
        "selection_bg": 51,
        "scrollbar": 51,
        "footer_fg": 255,
        "footer_bg": 238,
    },
    "nord": {
        "accent": 110,
        "muted": 102,
        "warning": 222,
        "error": 174,
        "success": 108,
        "secondary": 139,
        "selection_fg": 235,
        "selection_bg": 110,
        "scrollbar": 110,
        "footer_fg": 252,
        "footer_bg": 237,
    },
    "dracula": {
        "accent": 117,
        "muted": 103,
        "warning": 228,
        "error": 210,
        "success": 84,
        "secondary": 212,
        "selection_fg": 232,
        "selection_bg": 141,
        "scrollbar": 141,
        "footer_fg": 255,
        "footer_bg": 236,
    },
    "solarized": {
        "accent": 37,
        "muted": 244,
        "warning": 136,
        "error": 160,
        "success": 64,
        "secondary": 125,
        "selection_fg": 230,
        "selection_bg": 37,
        "scrollbar": 37,
        "footer_fg": 230,
        "footer_bg": 234,
    },
}

PALETTE_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("accent", "Accent / focused borders"),
    ("muted", "Muted text / inactive chrome"),
    ("warning", "Warnings"),
    ("error", "Errors"),
    ("success", "Success"),
    ("secondary", "Secondary / remote"),
    ("selection_fg", "Selection foreground"),
    ("selection_bg", "Selection background"),
    ("scrollbar", "Scrollbar thumb"),
    ("footer_fg", "Footer foreground"),
    ("footer_bg", "Footer background"),
)

_ACTIVE_UI_PALETTE_NAME = "default"
_ACTIVE_UI_PALETTE = deepcopy(BUILTIN_PALETTES["default"])
_ACTIVE_SCROLLBAR_STYLE = "rail"
_ORIGINAL_PAINT = core.paint
_ORIGINAL_REVERSE = core.REVERSE
_ORIGINAL_SELECTED_SEARCH_STYLE = pane_module.SELECTED_SEARCH_STYLE


# Styles used by the compatibility renderer and current application layer.
_STYLE_ROLES = {
    "\x1b[1;96m": "accent",
    "\x1b[36m": "accent",
    "\x1b[2m": "muted",
    "\x1b[1;93m": "warning",
    "\x1b[33m": "warning",
    "\x1b[1;91m": "error",
    "\x1b[32m": "success",
    "\x1b[35m": "secondary",
    "\x1b[1;95m": "secondary",
}
_SELECTION_STYLES = {
    "\x1b[1;30;106m",
    "\x1b[1;30;105m",
    "\x1b[1;30;48;5;208m",
}


def _load_state() -> dict:
    try:
        state = core._load_app_state()
    except Exception:
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(state: dict) -> None:
    try:
        core._save_app_state(state)
    except Exception:
        pass


def _clamp_color(value: object, fallback: int) -> int:
    try:
        return max(0, min(255, int(value)))
    except (TypeError, ValueError):
        return fallback


def _normalize_palette(values: Mapping[str, object], base: Optional[Mapping[str, int]] = None) -> Dict[str, int]:
    defaults = dict(base or BUILTIN_PALETTES["default"])
    return {
        field: _clamp_color(values.get(field), defaults[field])
        for field, _label in PALETTE_FIELDS
    }


def custom_palettes() -> Dict[str, Dict[str, int]]:
    raw = _load_state().get(UI_PALETTES_STATE_KEY, {})
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, Dict[str, int]] = {}
    for raw_name, values in raw.items():
        name = str(raw_name).strip()
        if not name or name in BUILTIN_PALETTES or not isinstance(values, dict):
            continue
        result[name] = _normalize_palette(values)
    return result


def all_palettes() -> Dict[str, Dict[str, int]]:
    result = {name: deepcopy(values) for name, values in BUILTIN_PALETTES.items()}
    result.update(custom_palettes())
    return result


def current_palette_name() -> str:
    return _ACTIVE_UI_PALETTE_NAME


def current_palette() -> Dict[str, int]:
    return dict(_ACTIVE_UI_PALETTE)


def current_scrollbar_style() -> str:
    return _ACTIVE_SCROLLBAR_STYLE


def _apply_scrollbar_style(name: str, *, persist: bool) -> str:
    global _ACTIVE_SCROLLBAR_STYLE
    candidate = name if name in SCROLLBAR_STYLES else "rail"
    _ACTIVE_SCROLLBAR_STYLE = candidate
    if persist:
        state = _load_state()
        state[SCROLLBAR_STYLE_STATE_KEY] = candidate
        _save_state(state)
    return candidate


def _load_scrollbar_style() -> None:
    requested = str(_load_state().get(SCROLLBAR_STYLE_STATE_KEY, "rail")).strip().lower()
    _apply_scrollbar_style(requested, persist=False)


def _scrollbar_gutter_width(style: Optional[str] = None) -> int:
    return 2 if (style or current_scrollbar_style()) in {"rail", "minimal"} else 0


def _fg(index: int, *, bold: bool = False, dim: bool = False) -> str:
    attrs: List[str] = []
    if bold:
        attrs.append("1")
    if dim:
        attrs.append("2")
    attrs.extend(("38", "5", str(index)))
    return "\x1b[" + ";".join(attrs) + "m"


def _pair(fg: int, bg: int, *, bold: bool = False) -> str:
    attrs = ["1"] if bold else []
    attrs.extend(("38", "5", str(fg), "48", "5", str(bg)))
    return "\x1b[" + ";".join(attrs) + "m"


def _selection_style() -> str:
    return _pair(_ACTIVE_UI_PALETTE["selection_fg"], _ACTIVE_UI_PALETTE["selection_bg"], bold=True)


def _mapped_style(style: str) -> str:
    if _ACTIVE_UI_PALETTE_NAME == "default":
        return style
    if style in _SELECTION_STYLES:
        return _selection_style()
    role = _STYLE_ROLES.get(style)
    if role is None:
        return style
    if role == "muted":
        return _fg(_ACTIVE_UI_PALETTE[role], dim=True)
    return _fg(_ACTIVE_UI_PALETTE[role], bold=style.startswith("\x1b[1;"))


def _themed_paint(text: str, style: str, enabled: bool) -> str:
    return _ORIGINAL_PAINT(text, _mapped_style(style), enabled)


def _apply_palette(name: str, *, persist: bool) -> str:
    global _ACTIVE_UI_PALETTE_NAME, _ACTIVE_UI_PALETTE
    palettes = all_palettes()
    candidate = name if name in palettes else "default"
    _ACTIVE_UI_PALETTE_NAME = candidate
    _ACTIVE_UI_PALETTE = dict(palettes[candidate])
    # The footer is emitted by concatenating core.REVERSE directly, so theme it
    # separately from core.paint-based UI chrome.
    if candidate == "default":
        core.REVERSE = _ORIGINAL_REVERSE
        pane_module.SELECTED_SEARCH_STYLE = _ORIGINAL_SELECTED_SEARCH_STYLE
    else:
        core.REVERSE = _pair(_ACTIVE_UI_PALETTE["footer_fg"], _ACTIVE_UI_PALETTE["footer_bg"])
        pane_module.SELECTED_SEARCH_STYLE = _selection_style()
    if persist:
        state = _load_state()
        state[UI_PALETTE_STATE_KEY] = candidate
        _save_state(state)
    return candidate


def _load_active_palette() -> None:
    state = _load_state()
    requested = str(state.get(UI_PALETTE_STATE_KEY, "default")).strip()
    _apply_palette(requested, persist=False)


def _store_custom_palette(name: str, values: Mapping[str, object]) -> None:
    if name in BUILTIN_PALETTES:
        raise ValueError("built-in palettes are read-only")
    state = _load_state()
    custom = state.get(UI_PALETTES_STATE_KEY, {})
    custom = dict(custom) if isinstance(custom, dict) else {}
    custom[name] = _normalize_palette(values)
    state[UI_PALETTES_STATE_KEY] = custom
    state[UI_PALETTE_STATE_KEY] = name
    _save_state(state)
    _apply_palette(name, persist=False)


def _delete_custom_palette(name: str) -> bool:
    if name in BUILTIN_PALETTES:
        return False
    state = _load_state()
    custom = state.get(UI_PALETTES_STATE_KEY, {})
    if not isinstance(custom, dict) or name not in custom:
        return False
    custom = dict(custom)
    custom.pop(name, None)
    state[UI_PALETTES_STATE_KEY] = custom
    if state.get(UI_PALETTE_STATE_KEY) == name:
        state[UI_PALETTE_STATE_KEY] = "default"
    _save_state(state)
    if current_palette_name() == name:
        _apply_palette("default", persist=False)
    return True


def _visible_boundaries(text: str) -> Tuple[str, List[int]]:
    plain = core.strip_ansi(text)
    boundaries = [0] * (len(plain) + 1)
    raw = visible = 0
    while raw < len(text) and visible < len(plain):
        match = core.ANSI_RE.match(text, raw)
        if match is not None:
            raw = match.end()
            continue
        boundaries[visible] = raw
        visible += 1
        raw += 1
    boundaries[visible] = raw
    return plain, boundaries


def _replace_visible_cell(text: str, column: int, replacement: str) -> str:
    plain, boundaries = _visible_boundaries(text)
    if column < 0:
        column += len(plain)
    if column < 0 or column >= len(plain):
        return text
    start = boundaries[column]
    end = boundaries[column + 1]
    return text[:start] + replacement + text[end:]


def _style_visible_span(text: str, start: int, end: int, style: str) -> str:
    plain, boundaries = _visible_boundaries(text)
    start = max(0, min(len(plain), start))
    end = max(start, min(len(plain), end))
    if end <= start:
        return text
    raw_start = boundaries[start]
    raw_end = boundaries[end]
    restore = pane_module._active_sgr_prefix(text, raw_start)
    selected = core.strip_ansi(text[raw_start:raw_end])
    return text[:raw_start] + style + selected + core.RESET + restore + text[raw_end:]


def _insert_before_last_visible(text: str, insertion: str) -> str:
    plain, boundaries = _visible_boundaries(text)
    if not plain:
        return text + insertion
    raw = boundaries[len(plain) - 1]
    return text[:raw] + insertion + text[raw:]


def _selection_bounds(selection: MouseSelection) -> Tuple[int, int, int, int]:
    start = (selection.start_visual, selection.start_col)
    end = (selection.end_visual, selection.end_col)
    if start <= end:
        return selection.start_visual, selection.start_col, selection.end_visual, selection.end_col
    return selection.end_visual, selection.end_col, selection.start_visual, selection.start_col


def _selection_text(
    visual_rows,
    start_visual: int,
    start_col: int,
    end_visual: int,
    end_col: int,
) -> str:
    if (start_visual, start_col) > (end_visual, end_col):
        start_visual, end_visual = end_visual, start_visual
        start_col, end_col = end_col, start_col
    pieces: List[str] = []
    for visual_index in range(start_visual, end_visual + 1):
        if visual_index < 0 or visual_index >= len(visual_rows):
            continue
        plain = core.strip_ansi(visual_rows[visual_index])
        left = start_col if visual_index == start_visual else 0
        right = end_col if visual_index == end_visual else len(plain)
        left = max(0, min(len(plain), left))
        right = max(left, min(len(plain), right))
        pieces.append(plain[left:right])
    return "\n".join(pieces)


def _scrollbar_geometry(total: int, viewport: int, top: int, track: int) -> Tuple[int, int]:
    track = max(0, track)
    if track <= 0:
        return 0, 0
    if total <= 0 or total <= viewport:
        return 0, 0
    viewport = max(1, viewport)
    thumb = max(1, min(track, int(round(track * (viewport / float(total))))))
    max_top = max(1, total - viewport)
    travel = max(0, track - thumb)
    offset = 0 if travel <= 0 else int(round(travel * (max(0, min(top, max_top)) / float(max_top))))
    return offset, thumb


def _pane_view_state(pane, body_h: int) -> Tuple[int, int]:
    if bool(getattr(pane, "prefer_snapshot", False)) and bool(getattr(pane, "snapshot_raw", None)):
        total = len(getattr(pane, "_snapshot_visual_lines", ()))
        top = int(getattr(pane, "_snapshot_top", 0))
    else:
        total = len(getattr(pane, "_visual_lines", ()))
        top = int(getattr(pane, "top", 0))
    return total, top


def _pane_selection_visible_start(pane) -> int:
    if int(getattr(pane, "horizontal_offset", 0)) and (
        not bool(getattr(pane, "wrap_enabled", True))
        or bool(getattr(pane, "_mouse_selection_table", False))
    ):
        return int(getattr(pane, "horizontal_offset", 0))
    return 0


def _install_pane_chrome() -> None:
    Pane = pane_module.Pane
    if getattr(Pane, "_htail_ui_features_extension", False):
        return
    original_init = Pane.__init__
    original_render_box = Pane.render_box

    def pane_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        self._mouse_selection = None
        self._mouse_selection_table = False

    def clear_mouse_selection(self) -> None:
        self._mouse_selection = None

    def render_box(self, width: int, height: int, focused: bool, index: int):
        style = current_scrollbar_style()
        gutter = _scrollbar_gutter_width(style) if width >= 8 else 0
        base_width = max(4, width - gutter)
        rows = list(original_render_box(self, base_width, height, focused, index))
        if base_width < 4 or height < 3 or len(rows) < 3:
            return rows

        body_h = min(max(0, height - 2), max(0, len(rows) - 2))
        total, top = _pane_view_state(self, body_h)
        thumb_start, thumb_len = _scrollbar_geometry(total, body_h, top, body_h)

        if gutter:
            # The new default keeps the pane border visually independent from
            # the scrollbar: [content][bar][space][border].
            rows[0] = _insert_before_last_visible(rows[0], "─" * gutter)
            rows[-1] = _insert_before_last_visible(rows[-1], "─" * gutter)
            for local in range(body_h):
                row_index = 1 + local
                if row_index >= len(rows) - 1:
                    break
                thumb = bool(thumb_len and thumb_start <= local < thumb_start + thumb_len)
                if style == "rail":
                    glyph = "█" if thumb else "│"
                else:  # minimal
                    glyph = "▐" if thumb else " "
                if bool(getattr(self, "color", False)) and thumb and focused:
                    bar = core.paint(glyph, _fg(_ACTIVE_UI_PALETTE["scrollbar"], bold=True), True)
                elif bool(getattr(self, "color", False)):
                    # Inactive panes deliberately carry no palette foreground
                    # colour on their scrollbar; only the focused thumb is accented.
                    bar = _ORIGINAL_PAINT(glyph, "\x1b[2m", True)
                else:
                    bar = glyph
                plain, boundaries = _visible_boundaries(rows[row_index])
                if not plain:
                    continue
                raw = boundaries[len(plain) - 1]
                restore = pane_module._active_sgr_prefix(rows[row_index], raw)
                rows[row_index] = (
                    rows[row_index][:raw]
                    + core.RESET
                    + bar
                    + core.RESET
                    + " "
                    + restore
                    + rows[row_index][raw:]
                )
        elif style == "border" and thumb_len:
            for local in range(body_h):
                if not (thumb_start <= local < thumb_start + thumb_len):
                    continue
                row_index = 1 + local
                if row_index >= len(rows) - 1:
                    break
                replacement = "┃"
                if bool(getattr(self, "color", False)) and focused:
                    replacement = core.paint(
                        "┃", _fg(_ACTIVE_UI_PALETTE["scrollbar"], bold=True), True
                    )
                rows[row_index] = _replace_visible_cell(rows[row_index], -1, replacement)

        selection = getattr(self, "_mouse_selection", None)
        if isinstance(selection, MouseSelection) and bool(getattr(self, "color", False)):
            snapshot = bool(getattr(self, "prefer_snapshot", False) and getattr(self, "snapshot_raw", None))
            current_mode = "snapshot" if snapshot else "history"
            current_top = int(getattr(self, "_snapshot_top", 0) if snapshot else getattr(self, "top", 0))
            visual_rows = (
                getattr(self, "_snapshot_visual_lines", ())
                if snapshot
                else getattr(self, "_visual_lines", ())
            )
            if selection.mode == current_mode:
                start_visual, start_col, end_visual, end_col = _selection_bounds(selection)
                viewport_start = _pane_selection_visible_start(self)
                content_width = max(0, base_width - 2)
                for visual_index in range(
                    max(start_visual, current_top),
                    min(end_visual, current_top + body_h - 1) + 1,
                ):
                    local = visual_index - current_top
                    row_index = 1 + local
                    if not (0 <= row_index < len(rows) - 1) or not (0 <= visual_index < len(visual_rows)):
                        continue
                    plain_source = core.strip_ansi(visual_rows[visual_index])
                    row_start = start_col if visual_index == start_visual else 0
                    row_end = end_col if visual_index == end_visual else len(plain_source)
                    visible_start = max(0, row_start - viewport_start)
                    visible_end = min(content_width, row_end - viewport_start)
                    if visible_end > visible_start:
                        rows[row_index] = _style_visible_span(
                            rows[row_index],
                            1 + visible_start,
                            1 + visible_end,
                            _selection_style(),
                        )
        return rows

    Pane.__init__ = pane_init
    Pane.render_box = render_box
    Pane.clear_mouse_selection = clear_mouse_selection
    Pane._htail_ui_features_extension = True


def _interval_overlap(a1: float, a2: float, b1: float, b2: float) -> float:
    return max(0.0, min(a2, b2) - max(a1, b1))


def _directional_neighbor(rects, current_index: int, direction: str) -> Optional[int]:
    current = next((rect for index, rect in rects if index == current_index), None)
    if current is None:
        return None
    cx = current.x + current.width / 2.0
    cy = current.y + current.height / 2.0
    candidates = []
    for index, rect in rects:
        if index < 0 or index == current_index:
            continue
        rx = rect.x + rect.width / 2.0
        ry = rect.y + rect.height / 2.0
        if direction == "left" and rx >= cx:
            continue
        if direction == "right" and rx <= cx:
            continue
        if direction == "up" and ry >= cy:
            continue
        if direction == "down" and ry <= cy:
            continue
        if direction in ("left", "right"):
            overlap = _interval_overlap(current.y, current.y + current.height, rect.y, rect.y + rect.height)
            orth = abs(ry - cy)
            primary = abs(rx - cx)
        else:
            overlap = _interval_overlap(current.x, current.x + current.width, rect.x, rect.x + rect.width)
            orth = abs(rx - cx)
            primary = abs(ry - cy)
        candidates.append(((0 if overlap > 0 else 1, primary, orth), index))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _selection_word(text: str, column: int) -> Optional[Tuple[int, int, str]]:
    if not text:
        return None
    column = max(0, min(column, len(text) - 1))
    if text[column].isspace():
        return None
    token_re = re.compile(r"[\w./:@+~$%-]+|[^\s]")
    for match in token_re.finditer(text):
        if match.start() <= column < match.end():
            return match.start(), match.end(), match.group(0)
    return None


def _osc52_copy(text: str) -> None:
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    sys.stdout.write(f"\x1b]52;c;{payload}\x07")
    sys.stdout.flush()


def _invalidate_app_render(app) -> None:
    cache = getattr(app, "_render_pane_box_cache", None)
    if isinstance(cache, dict):
        cache.clear()
    app._last_frame = None
    app._last_frame_geometry = None
    for pane in [*getattr(app, "panes", ()), getattr(app, "stream", None)]:
        if pane is None:
            continue
        viewport = getattr(pane, "_viewport_row_cache", None)
        if hasattr(viewport, "clear"):
            viewport.clear()
    app.dirty = True


def _install_input_keys() -> None:
    if getattr(input_module, "_htail_ui_input_extension", False):
        return
    original_parse = input_module.parse_escape_sequence

    alt_mapping = {
        "\x1b[1;3A": "ALT_UP",
        "\x1b[1;3B": "ALT_DOWN",
        "\x1b[1;3C": "ALT_RIGHT",
        "\x1b[1;3D": "ALT_LEFT",
        "\x1b\x1b[A": "ALT_UP",
        "\x1b\x1b[B": "ALT_DOWN",
        "\x1b\x1b[C": "ALT_RIGHT",
        "\x1b\x1b[D": "ALT_LEFT",
    }

    def parse_escape_sequence(seq: str):
        return alt_mapping.get(seq) or original_parse(seq)

    input_module.parse_escape_sequence = parse_escape_sequence

    # Windows terminal input exposes modifier state separately from the key.
    InputReader = input_module.InputReader
    original_windows = InputReader._poll_windows

    def poll_windows(self):
        event = original_windows(self)
        if event not in {"UP", "DOWN", "LEFT", "RIGHT"}:
            return event
        try:
            import ctypes
            VK_MENU = 0x12
            if ctypes.windll.user32.GetKeyState(VK_MENU) & 0x8000:
                return "ALT_" + event
        except Exception:
            pass
        return event

    InputReader._poll_windows = poll_windows
    input_module._htail_ui_input_extension = True


def _install_app_features() -> None:
    from . import app as app_module

    MultiApp = app_module.MultiApp
    if getattr(MultiApp, "_htail_ui_features_extension", False):
        return

    original_init = MultiApp.__init__
    original_handle_input = MultiApp.handle_input
    original_handle_mouse = MultiApp.handle_mouse
    original_palette_all_items = MultiApp._palette_all_items
    original_execute_palette_item = MultiApp._execute_palette_item
    original_palette_lines = MultiApp._palette_lines
    original_status_lines = MultiApp._status_lines

    def app_init(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        self._last_left_click_time = 0.0
        self._last_left_click_target = None
        self._last_left_click_xy = None
        self._mouse_drag_selection = None
        self._ui_theme_field = 0
        self._ui_theme_draft = None
        self._ui_theme_draft_name = None
        self._ui_theme_edit_existing = False

    def palette_all_items(self):
        if self.palette_mode == "ui-themes":
            active = current_palette_name()
            custom = custom_palettes()
            items = []
            for name in BUILTIN_PALETTES:
                prefix = "✓ " if name == active else ""
                items.append(app_module.PaletteItem(prefix + name, "ui-theme-select", name, "BUILT-IN"))
            for name in sorted(custom):
                prefix = "✓ " if name == active else ""
                items.append(app_module.PaletteItem(prefix + name, "ui-theme-select", name, "CUSTOM"))
            return items
        items = list(original_palette_all_items(self))
        if self.palette_mode == "commands":
            items.append(app_module.PaletteItem("UI themes / palette editor…", "ui-themes", detail="apply / create / edit / delete"))
            style = current_scrollbar_style()
            items.append(app_module.PaletteItem(
                f"Scrollbar style: {SCROLLBAR_LABELS[style]}",
                "scrollbar-style",
                detail="Enter cycles Rail / Border / Minimal / Off",
            ))
            if self.layout != "stream" and self.panes:
                items.append(app_module.PaletteItem("Close focused pane", "close-pane", detail="Ctrl+W"))
        return items

    def _open_ui_themes(self) -> None:
        self.palette_mode = "ui-themes"
        self.palette_buffer = ""
        self.palette_selected = 0
        self._refresh_palette()
        names = [str(item.value) for item in self.palette_items]
        if current_palette_name() in names:
            self.palette_selected = names.index(current_palette_name())
        self.dirty = True

    def _selected_ui_theme_name(self) -> str:
        self._refresh_palette()
        if not self.palette_items:
            return current_palette_name()
        index = min(max(0, self.palette_selected), len(self.palette_items) - 1)
        return str(self.palette_items[index].value)

    def _start_theme_name(self) -> None:
        base_name = _selected_ui_theme_name(self)
        base = all_palettes().get(base_name, current_palette())
        self._ui_theme_draft = dict(base)
        self._ui_theme_draft_name = None
        self._ui_theme_edit_existing = False
        self.palette_mode = "ui-theme-name"
        self.palette_buffer = ""
        self.dirty = True

    def _start_theme_edit(self, name: str) -> None:
        palettes = all_palettes()
        if name in BUILTIN_PALETTES:
            self.set_message("built-in palettes are read-only; press n to copy one", 4.0)
            return
        if name not in palettes:
            return
        self._ui_theme_draft_name = name
        self._ui_theme_draft = dict(palettes[name])
        self._ui_theme_edit_existing = True
        self._ui_theme_field = 0
        self.palette_mode = "ui-theme-editor"
        self.dirty = True

    def _save_theme_draft(self) -> bool:
        name = str(self._ui_theme_draft_name or "").strip()
        draft = self._ui_theme_draft
        if not name or not isinstance(draft, dict):
            return False
        _store_custom_palette(name, draft)
        _invalidate_app_render(self)
        self.set_message(f"UI palette saved: {name}", 3.0)
        self.palette_mode = "ui-themes"
        self.palette_buffer = ""
        self._refresh_palette()
        names = [str(item.value) for item in self.palette_items]
        if name in names:
            self.palette_selected = names.index(name)
        return True

    def _close_focused_pane(self) -> bool:
        if self.layout == "stream" or not self.panes:
            self.set_message("switch to a pane layout before closing a pane", 3.0)
            return False
        index = min(max(0, self.focus), len(self.panes) - 1)
        pane = self.panes[index]
        follower = self.followers[index]
        close = getattr(follower, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        del self.panes[index]
        del self.followers[index]
        # Keep _known_file_paths intact: a deliberately closed dynamic-glob pane
        # must not immediately reappear on the next glob scan.
        for layout_name, weights in list(self._pane_layout_weights.items()):
            if index < len(weights):
                del weights[index]
            self._pane_layout_weights[layout_name] = weights[: len(self.panes)]
        self.focus = min(index, max(0, len(self.panes) - 1))
        self.maximized = False
        self.last_rects = []
        self._global_search_corpus_signature = None
        self._global_search_cache_key = None
        if not self.panes:
            self.layout = "stream"
        _invalidate_app_render(self)
        self.set_message(f"closed pane: {pane.name}", 3.0)
        return True

    def _focus_direction(self, direction: str) -> bool:
        if self.layout == "stream" or len(self.panes) < 2:
            return False
        rects = list(self.last_rects)
        if self.maximized or len([index for index, _rect in rects if index >= 0]) < len(self.panes):
            width, height, _footer = self.content_dimensions()
            layout_name = self.resolved_layout(width, height)
            weights = self._layout_weights(layout_name, len(self.panes)) if layout_name in ("rows", "columns") else None
            generated = app_module.pane_rects(layout_name, len(self.panes), width, height, weights)
            rects = list(enumerate(generated))
        target = _directional_neighbor(rects, self.focus, direction)
        if target is None or target == self.focus:
            return False
        previous = self.focus
        self.focus = target
        marker = getattr(self, "_mark_panes_dirty", None)
        if callable(marker):
            marker((previous, target))
        else:
            self.dirty = True
        if hasattr(self, "_terminal_scroll_hint"):
            self._terminal_scroll_hint = None
        return True

    def _clear_other_selections(self, target) -> None:
        for pane in [*self.panes, self.stream]:
            if pane is not target and hasattr(pane, "clear_mouse_selection"):
                pane.clear_mouse_selection()

    def _mouse_point(self, event, target_index: int, pane, rect, *, clamp: bool = False):
        focused = target_index == self.focus if target_index >= 0 else True
        inline_search = getattr(self, "prompt_mode", None) == "search" and focused and rect.height >= 4
        match_status = getattr(pane, "search_regex", None) is not None and rect.height >= (5 if inline_search else 4)
        reserve = (1 if inline_search else 0) + (1 if match_status else 0)
        render_height = rect.height - reserve
        body_h = max(0, render_height - 2)
        local_y = event.y - rect.y - 1 - (1 if match_status else 0)
        if clamp and body_h:
            local_y = min(max(0, local_y), body_h - 1)
        if local_y < 0 or local_y >= body_h:
            return None

        style = current_scrollbar_style()
        gutter = _scrollbar_gutter_width(style) if rect.width >= 8 else 0
        inner_w = max(1, rect.width - 2 - gutter)
        snapshot = bool(pane.prefer_snapshot and pane.snapshot_raw)
        if snapshot:
            pane._ensure_snapshot_layout(inner_w)
            visual = pane._snapshot_visual_lines
            top = pane._snapshot_top
            mode = "snapshot"
        else:
            pane._ensure_layout(inner_w)
            visual = pane._visual_lines
            top = pane.top
            mode = "history"
        visual_index = top + local_y
        if clamp and visual:
            visual_index = min(max(0, visual_index), len(visual) - 1)
        if visual_index < 0 or visual_index >= len(visual):
            return None

        raw_row = visual[visual_index]
        table = bool(pane._is_markdown_table_visual(raw_row))
        viewport_start = pane.horizontal_offset if pane.horizontal_offset and (not pane.wrap_enabled or table) else 0
        content_x = event.x - rect.x - 1
        if clamp:
            content_x = min(max(0, content_x), inner_w - 1)
        if content_x < 0 or content_x >= inner_w:
            return None
        plain = core.strip_ansi(raw_row)
        full_column = viewport_start + content_x
        if clamp:
            full_column = min(max(0, full_column), len(plain))
        elif full_column < 0 or full_column >= len(plain):
            return None
        return mode, visual, visual_index, full_column, plain, table

    def _set_mouse_selection(
        self,
        pane,
        mode: str,
        visual,
        start_visual: int,
        start_col: int,
        end_visual: int,
        end_col: int,
    ) -> MouseSelection:
        text = _selection_text(visual, start_visual, start_col, end_visual, end_col)
        selection = MouseSelection(
            mode, start_visual, start_col, end_visual, end_col, text
        )
        pane._mouse_selection = selection
        marker = getattr(self, "_mark_pane_dirty", None)
        if callable(marker):
            try:
                marker(-1 if pane is self.stream else self.panes.index(pane))
            except ValueError:
                self.dirty = True
        else:
            self.dirty = True
        return selection

    def _start_mouse_word_selection(self, event, target_index: int, pane, rect) -> bool:
        point = _mouse_point(self, event, target_index, pane, rect)
        if point is None:
            return False
        mode, visual, visual_index, full_column, plain, table = point
        if full_column >= len(plain):
            return False
        selected = _selection_word(plain, full_column)
        if selected is None:
            return False
        start, end, text = selected
        _clear_other_selections(self, pane)
        pane._mouse_selection_table = table
        selection = _set_mouse_selection(
            self, pane, mode, visual, visual_index, start, visual_index, end
        )
        self._mouse_drag_selection = {
            "target": target_index,
            "pane": pane,
            "mode": mode,
            "visual": visual,
            "anchor_visual": visual_index,
            "anchor_start": start,
            "anchor_end": end,
            "dragged": False,
        }
        try:
            _osc52_copy(selection.text)
            pane.set_message(f"copied: {text}", 2.5)
        except Exception:
            pane.set_message(f"selected: {text}", 2.5)
        return True

    def _extend_mouse_selection(self, event) -> bool:
        drag = self._mouse_drag_selection
        if not isinstance(drag, dict):
            return False
        target = int(drag["target"])
        rect = next((rect for index, rect in self.last_rects if index == target), None)
        if rect is None:
            return False
        pane = drag["pane"]
        point = _mouse_point(self, event, target, pane, rect, clamp=True)
        if point is None:
            return False
        mode, visual, visual_index, full_column, _plain, table = point
        if mode != drag["mode"]:
            return False
        pane._mouse_selection_table = table
        anchor_visual = int(drag["anchor_visual"])
        anchor_start = int(drag["anchor_start"])
        anchor_end = int(drag["anchor_end"])
        if (visual_index, full_column) < (anchor_visual, anchor_start):
            start_visual, start_col = visual_index, full_column
            end_visual, end_col = anchor_visual, anchor_end
        else:
            start_visual, start_col = anchor_visual, anchor_start
            end_visual, end_col = visual_index, full_column
            if 0 <= visual_index < len(visual):
                end_col = min(len(core.strip_ansi(visual[visual_index])), end_col + 1)
        _set_mouse_selection(
            self, pane, mode, visual, start_visual, start_col, end_visual, end_col
        )
        drag["dragged"] = True
        return True

    def _finish_mouse_selection(self) -> bool:
        drag = self._mouse_drag_selection
        self._mouse_drag_selection = None
        if not isinstance(drag, dict):
            return False
        pane = drag["pane"]
        selection = getattr(pane, "_mouse_selection", None)
        if not isinstance(selection, MouseSelection):
            return False
        if drag.get("dragged"):
            try:
                _osc52_copy(selection.text)
                pane.set_message(
                    f"copied selection ({len(selection.text)} chars)", 2.5
                )
            except Exception:
                pane.set_message(
                    f"selected ({len(selection.text)} chars)", 2.5
                )
        return True

    def execute_palette_item(self) -> None:
        self._refresh_palette()
        if not self.palette_items:
            return
        item = self.palette_items[self.palette_selected]
        if item.action == "ui-themes":
            _open_ui_themes(self)
            return
        if item.action == "ui-theme-select":
            _apply_palette(str(item.value), persist=True)
            _invalidate_app_render(self)
            self.set_message(f"UI palette: {current_palette_name()}", 3.0)
            self.palette_active = False
            return
        if item.action == "scrollbar-style":
            current = current_scrollbar_style()
            next_index = (SCROLLBAR_STYLES.index(current) + 1) % len(SCROLLBAR_STYLES)
            selected = _apply_scrollbar_style(SCROLLBAR_STYLES[next_index], persist=True)
            _invalidate_app_render(self)
            self.set_message(f"scrollbar style: {SCROLLBAR_LABELS[selected]}", 3.0)
            self.palette_active = False
            return
        if item.action == "close-pane":
            _close_focused_pane(self)
            self.palette_active = False
            return
        return original_execute_palette_item(self)

    def palette_lines(self, width: int, height: int):
        if self.palette_mode == "ui-theme-name":
            rows = [
                core.paint("Name: ", core.BOLD_LIGHT_CYAN, self.color) + self.palette_buffer + "▌",
                "",
                "Create a custom palette by copying the currently selected palette.",
                "Names may contain letters, numbers, spaces, '.', '_' and '-'.",
                "",
                "Enter continue · Esc back · Backspace edit",
            ]
            return app_module._panel_lines("New UI palette", rows, width, height, self.color, max_width=86, min_width=48)
        if self.palette_mode == "ui-theme-editor":
            draft = self._ui_theme_draft or current_palette()
            name = self._ui_theme_draft_name or "custom"
            rows = [core.paint(name, core.BOLD_LIGHT_CYAN, self.color), ""]
            for index, (field, label) in enumerate(PALETTE_FIELDS):
                value = int(draft[field])
                sample = core.paint(" SAMPLE ", _fg(value, bold=True), self.color)
                prefix = "› " if index == self._ui_theme_field else "  "
                line = f"{prefix}{label:<30} {value:>3}  {sample}"
                if index == self._ui_theme_field:
                    line = core.paint(line, _selection_style(), self.color)
                rows.append(line)
            rows.extend([
                "",
                "↑/↓ field · ←/→ ±1 · PgUp/PgDn ±8 · s save/update · Esc/q cancel",
            ])
            return app_module._panel_lines("UI palette editor", rows, width, height, self.color, max_width=96, min_width=58)
        if self.palette_mode == "ui-themes":
            self._refresh_palette()
            rows = [
                core.paint("Choose a palette", core.BOLD_LIGHT_CYAN, self.color),
                "",
            ]
            start = max(0, min(self.palette_selected - 5, max(0, len(self.palette_items) - 10)))
            for index, item in enumerate(self.palette_items[start:start + 10], start=start):
                prefix = "› " if index == self.palette_selected else "  "
                line = f"{prefix}{item.label:<24} {item.detail}"
                if index == self.palette_selected:
                    line = core.paint(line, _selection_style(), self.color)
                rows.append(line)
            rows.extend([
                "",
                "Enter apply · n new copy · e edit custom · d delete custom · Esc/q close",
                "Built-in palettes are read-only.",
            ])
            return app_module._panel_lines("UI themes", rows, width, height, self.color, max_width=88, min_width=52)
        return original_palette_lines(self, width, height)

    def handle_theme_input(self, key) -> Optional[bool]:
        if not self.palette_active or self.palette_mode not in {"ui-themes", "ui-theme-name", "ui-theme-editor"}:
            return None
        if not isinstance(key, str):
            burst_key = getattr(key, "key", None)
            burst_count = int(getattr(key, "count", 0) or 0)
            if isinstance(burst_key, str) and burst_count > 0:
                result = False
                for _ in range(min(burst_count, 12)):
                    result = bool(handle_theme_input(self, burst_key)) or result
                return result
            return False

        if self.palette_mode == "ui-theme-name":
            if key == "ESC":
                self.palette_mode = "ui-themes"; self.palette_buffer = ""; self.dirty = True; return False
            if key in ("\x7f", "\b"):
                self.palette_buffer = self.palette_buffer[:-1]; self.dirty = True; return False
            if key in ("\r", "\n"):
                name = self.palette_buffer.strip()
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}", name):
                    self.set_message("invalid palette name", 3.0); self.dirty = True; return False
                if name in BUILTIN_PALETTES:
                    self.set_message("built-in palette names are reserved", 3.0); self.dirty = True; return False
                if name in custom_palettes():
                    self.set_message("palette already exists; edit it instead", 3.0); self.dirty = True; return False
                self._ui_theme_draft_name = name
                self._ui_theme_field = 0
                self.palette_mode = "ui-theme-editor"
                self.palette_buffer = ""
                self.dirty = True
                return False
            if len(key) == 1 and key.isprintable() and len(self.palette_buffer) < 40:
                self.palette_buffer += key; self.dirty = True
            return False

        if self.palette_mode == "ui-theme-editor":
            if key in ("ESC", "q", "Q"):
                self.palette_mode = "ui-themes"; self._ui_theme_draft = None; self.dirty = True; return False
            if key in ("UP", "DOWN"):
                delta = -1 if key == "UP" else 1
                self._ui_theme_field = (self._ui_theme_field + delta) % len(PALETTE_FIELDS)
                self.dirty = True; return False
            if key in ("LEFT", "RIGHT", "PAGEUP", "PAGEDOWN"):
                step = {"LEFT": -1, "RIGHT": 1, "PAGEUP": 8, "PAGEDOWN": -8}[key]
                field = PALETTE_FIELDS[self._ui_theme_field][0]
                assert isinstance(self._ui_theme_draft, dict)
                self._ui_theme_draft[field] = (int(self._ui_theme_draft[field]) + step) % 256
                self.dirty = True; return False
            if key in ("s", "S"):
                _save_theme_draft(self); self.dirty = True; return False
            return False

        # ui-themes list
        if key in ("ESC", "q", "Q"):
            self.palette_active = False; self.dirty = True; return False
        if key in ("UP", "DOWN", "PAGEUP", "PAGEDOWN"):
            self._refresh_palette()
            if self.palette_items:
                delta = {"UP": -1, "DOWN": 1, "PAGEUP": -8, "PAGEDOWN": 8}[key]
                self.palette_selected = min(max(0, self.palette_selected + delta), len(self.palette_items) - 1)
            self.dirty = True; return False
        if key in ("\r", "\n"):
            name = _selected_ui_theme_name(self)
            _apply_palette(name, persist=True)
            _invalidate_app_render(self)
            self.palette_active = False
            self.set_message(f"UI palette: {name}", 3.0)
            return False
        if key in ("n", "N"):
            _start_theme_name(self); return False
        if key in ("e", "E"):
            _start_theme_edit(self, _selected_ui_theme_name(self)); return False
        if key in ("d", "D"):
            name = _selected_ui_theme_name(self)
            if name in BUILTIN_PALETTES:
                self.set_message("built-in palettes cannot be deleted", 3.0)
            elif _delete_custom_palette(name):
                self.set_message(f"deleted UI palette: {name}", 3.0)
                self._refresh_palette()
                self.palette_selected = min(self.palette_selected, max(0, len(self.palette_items) - 1))
                _invalidate_app_render(self)
            self.dirty = True; return False
        return False


    def status_lines(self, width: int, height: int):
        rows = list(original_status_lines(self, width, height))
        if len(rows) > 1:
            rows[1] += " · Alt+arrows pane · Ctrl+W close · double-click/drag copy"
        return rows

    def handle_input(self, event):
        handled = handle_theme_input(self, event)
        if handled is not None:
            return handled
        if not any(
            bool(getattr(self, name, False))
            for name in (
                "palette_active",
                "global_search_active",
                "prompt_mode",
                "update_confirm_active",
                "layout_menu",
                "help_active",
            )
        ):
            if event in {"ALT_UP", "ALT_DOWN", "ALT_LEFT", "ALT_RIGHT"}:
                direction = str(event).split("_", 1)[1].lower()
                _focus_direction(self, direction)
                return False
            if event == "CTRL_W":
                _close_focused_pane(self)
                return False
        return original_handle_input(self, event)

    def handle_mouse(self, event) -> None:
        # Drag motion and release are selection-only events. Do not feed them to
        # the compatibility click handler: that path marks the whole frame dirty
        # on every left-button event and would reintroduce visible drag flicker.
        if getattr(event, "button", None) == "left" and getattr(event, "motion", False):
            if getattr(event, "pressed", False):
                _extend_mouse_selection(self, event)
            return None
        if getattr(event, "button", None) == "left" and not getattr(event, "pressed", False):
            _finish_mouse_selection(self)
            return None

        result = original_handle_mouse(self, event)
        if getattr(event, "button", None) != "left":
            return result

        target = self._pane_at(event.x, event.y)
        if target is None:
            return result
        # A normal click in another pane makes that pane the sole possible
        # selection owner. The existing selection survives clicks inside its
        # own pane until a new selection starts.
        pane = self.stream if target == -1 else self.panes[target]
        _clear_other_selections(self, pane)

        now = time.monotonic()
        same_target = target == self._last_left_click_target
        last_xy = self._last_left_click_xy
        nearby = last_xy is not None and abs(event.x - last_xy[0]) <= 1 and abs(event.y - last_xy[1]) <= 1
        is_double = same_target and nearby and now - self._last_left_click_time <= DOUBLE_CLICK_SECONDS
        self._last_left_click_time = now
        self._last_left_click_target = target
        self._last_left_click_xy = (event.x, event.y)
        if not is_double:
            self._mouse_drag_selection = None
            return result

        self._last_left_click_time = 0.0
        rect = next((rect for index, rect in self.last_rects if index == target), None)
        if rect is None:
            return result
        _start_mouse_word_selection(self, event, target, pane, rect)
        return result

    MultiApp.__init__ = app_init
    MultiApp._palette_all_items = palette_all_items
    MultiApp._open_ui_themes = _open_ui_themes
    MultiApp._close_focused_pane = _close_focused_pane
    MultiApp._focus_direction = _focus_direction
    MultiApp._execute_palette_item = execute_palette_item
    MultiApp._palette_lines = palette_lines
    MultiApp._status_lines = status_lines
    MultiApp.handle_input = handle_input
    MultiApp.handle_mouse = handle_mouse
    MultiApp._htail_ui_features_extension = True


def install() -> None:
    if getattr(core, "_htail_ui_features_extension", False):
        return
    core.paint = _themed_paint
    _load_active_palette()
    _load_scrollbar_style()
    _install_pane_chrome()
    _install_input_keys()
    _install_app_features()
    core._htail_ui_features_extension = True
