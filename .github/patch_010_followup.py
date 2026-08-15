from pathlib import Path


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


path = Path("src/htail_app/pane.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "        self.search_pattern = \"\"\n        self.search_regex: Optional[Pattern[str]] = None\n        self.highlight_pattern = \"\"\n",
    "        self.search_pattern = \"\"\n        self.search_regex: Optional[Pattern[str]] = None\n        self._search_last_target: Optional[int] = None\n        self.highlight_pattern = \"\"\n",
    "search target state",
)
text = replace_once(
    text,
    "            self.search_regex = None\n            self._mark_layout_dirty()\n",
    "            self.search_regex = None\n            self._search_last_target = None\n            self._mark_layout_dirty()\n",
    "clear search target",
)
text = replace_once(
    text,
    "        self.search_pattern = expression\n        self.search_regex = compiled\n        self._mark_layout_dirty()\n",
    "        self.search_pattern = expression\n        self.search_regex = compiled\n        self._search_last_target = None\n        self._mark_layout_dirty()\n",
    "new search target",
)
text = replace_once(
    text,
    "        self.snapshot_raw = list(raw_lines)\n        self.snapshot_changed = set(changed_indices)\n",
    "        self.snapshot_raw = list(raw_lines)\n        self.snapshot_changed = set(changed_indices)\n        self._search_last_target = None\n",
    "snapshot resets search target",
)
old = '''            current_source = -1\n            if self._snapshot_visual_to_source:\n                start = min(max(0, self._snapshot_top), len(self._snapshot_visual_to_source) - 1)\n                for source in self._snapshot_visual_to_source[start:]:\n                    if source is not None:\n                        current_source = source\n                        break\n'''
new = '''            current_source = self._search_last_target if self._search_last_target is not None else -1\n            if self._search_last_target is None and self._snapshot_visual_to_source:\n                start = min(max(0, self._snapshot_top), len(self._snapshot_visual_to_source) - 1)\n                for source in self._snapshot_visual_to_source[start:]:\n                    if source is not None:\n                        current_source = source\n                        break\n'''
text = replace_once(text, old, new, "snapshot search cursor")
text = replace_once(
    text,
    "            position = candidates.index(target) + 1\n            self.set_message(f\"match {position}/{len(candidates)}: /{self.search_pattern}/\")\n",
    "            self._search_last_target = target\n            position = candidates.index(target) + 1\n            self.set_message(f\"match {position}/{len(candidates)}: /{self.search_pattern}/\")\n",
    "snapshot records match",
)
text = replace_once(
    text,
    "        current = self._logical_at_top()\n        if reverse:\n",
    "        current = self._search_last_target if self._search_last_target is not None else self._logical_at_top()\n        if reverse:\n",
    "history search cursor",
)
# The second occurrence of the position block is the history path.
needle = "        position = candidates.index(target) + 1\n        self.set_message(f\"match {position}/{len(candidates)}: /{self.search_pattern}/\")\n        return True\n"
if text.count(needle) != 1:
    raise RuntimeError(f"history records match: expected one remaining match, found {text.count(needle)}")
text = text.replace(
    needle,
    "        self._search_last_target = target\n        position = candidates.index(target) + 1\n        self.set_message(f\"match {position}/{len(candidates)}: /{self.search_pattern}/\")\n        return True\n",
    1,
)
path.write_text(text, encoding="utf-8")
print("0.10.0 search follow-up applied")
