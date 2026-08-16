from pathlib import Path


def replace_once(path, old, new, label):
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected 1 match, found {count}')
    p.write_text(text.replace(old, new, 1), encoding='utf-8')


# Always close OSC-8 hyperlink state when clipping a row. RESET does not close hyperlinks.
replace_once(
    'src/htail_app/core.py',
    '    # Ensure styles cannot leak into the status bar / following line.\n    out.append(RESET)\n',
    '    # Ensure styles and OSC-8 hyperlink state cannot leak into the following row.\n    if "\\x1b]8;;" in text:\n        out.append("\\x1b]8;;\\x1b\\\\")\n    out.append(RESET)\n',
    'OSC-8 close on clip',
)

# Slice first, then create links from the visible text. Also clamp horizontal scrolling.
replace_once(
    'src/htail_app/pane.py',
    '    def _viewport_row(self, row: str, width: int) -> str:\n        row = linkify_urls(row, self.color)\n        if not self.wrap_enabled and self.horizontal_offset:\n            row = self._slice_ansi(row, self.horizontal_offset, width)\n        return _pad_ansi(row, width)\n',
    '    def _viewport_row(self, row: str, width: int) -> str:\n        if not self.wrap_enabled and self.horizontal_offset:\n            row = self._slice_ansi(row, self.horizontal_offset, width)\n        row = linkify_urls(row, self.color)\n        return _pad_ansi(row, width)\n',
    'link after horizontal slice',
)
replace_once(
    'src/htail_app/pane.py',
    '    def scroll_horizontal(self, delta: int) -> None:\n        if self.wrap_enabled:\n            return\n        self.horizontal_offset = max(0, self.horizontal_offset + delta)\n',
    '    def scroll_horizontal(self, delta: int, width: Optional[int] = None) -> None:\n        if self.wrap_enabled:\n            return\n        target = max(0, self.horizontal_offset + delta)\n        if width is not None:\n            rows = self._snapshot_visual_lines if self.prefer_snapshot and self.snapshot_raw else self._visual_lines\n            max_width = max((len(core.strip_ansi(row)) for row in rows), default=0)\n            target = min(target, max(0, max_width - max(1, width)))\n        self.horizontal_offset = target\n',
    'horizontal clamp',
)

# Interactive --exec panes should inherit --heartbeat just like files/stdin/SSH.
replace_once(
    'src/htail_app/app.py',
    '            pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name=label)\n            follower = CommandFollower(command, args, label=label)\n',
    '            pane = Pane(pseudo, highlighter, display_filter, color, args.idle_warn, display_name=label, heartbeat_seconds=args.heartbeat)\n            follower = CommandFollower(command, args, label=label)\n',
    'command heartbeat inheritance',
)
replace_once(
    'src/htail_app/app.py',
    '        if key in ("LEFT", "RIGHT"):\n            pane.scroll_horizontal(-4 if key == "LEFT" else 4); self.dirty = True; return False\n',
    '        if key in ("LEFT", "RIGHT"):\n            inner_w, _ = self._active_pane_geometry()\n            pane.scroll_horizontal(-4 if key == "LEFT" else 4, inner_w); self.dirty = True; return False\n',
    'app horizontal clamp geometry',
)

# Additional hardening tests.
p = Path('tests/test_features_015.py')
t = p.read_text(encoding='utf-8')
t = t.replace(
    "        self.assertEqual(pane.horizontal_offset, 8)\n",
    "        self.assertEqual(pane.horizontal_offset, 8)\n        pane.scroll_horizontal(10000, 30)\n        max_width = max(len(core.strip_ansi(row)) for row in pane._snapshot_visual_lines)\n        self.assertLessEqual(pane.horizontal_offset, max(0, max_width - 30))\n",
    1,
)
t = t.replace(
    "        self.assertNotIn('\\x1b]8;;', core.strip_ansi(rendered))\n",
    "        self.assertNotIn('\\x1b]8;;', core.strip_ansi(rendered))\n        clipped = core.clip_ansi('\\x1b]8;;https://example.com\\x1b\\\\https://example.com/long/path\\x1b]8;;\\x1b\\\\', 8)\n        self.assertTrue(clipped.endswith('\\x1b]8;;\\x1b\\\\' + core.RESET))\n",
    1,
)
p.write_text(t, encoding='utf-8')

# Direct arrow decoder coverage.
p = Path('tests/test_multifile.py')
t = p.read_text(encoding='utf-8')
needle = '    def test_shift_tab(self):\n        self.assertEqual(parse_escape_sequence("\\x1b[Z"), "SHIFT_TAB")\n'
replacement = needle + '\n    def test_left_right_arrows(self):\n        self.assertEqual(parse_escape_sequence("\\x1b[D"), "LEFT")\n        self.assertEqual(parse_escape_sequence("\\x1b[C"), "RIGHT")\n'
if needle not in t:
    raise RuntimeError('arrow test insertion point missing')
p.write_text(t.replace(needle, replacement, 1), encoding='utf-8')
