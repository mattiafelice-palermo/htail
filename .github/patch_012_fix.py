from pathlib import Path
import runpy

path = Path('.github/patch_012.py')
text = path.read_text(encoding='utf-8')
old = '''    "        self._initial_bottom_pending = False\\n",
    "        self._initial_bottom_pending = False\\n        # Startup follows EOF until the user actually navigates or the first\\n        # update arrives. Unlike the legacy one-shot flag, this survives a\\n        # terminal/layout geometry change between the first two renders.\\n        self._startup_follow_eof = True\\n        self.follow_mode = FOLLOW_CHANGES\\n        self.tail_auto_follow = True\\n        self._snapshot_tail_pending = False\\n",
'''
new = '''    "        self._pending_anchor_logical: Optional[int] = None\\n        self._initial_bottom_pending = False\\n",
    "        self._pending_anchor_logical: Optional[int] = None\\n        self._initial_bottom_pending = False\\n        # Startup follows EOF until the user actually navigates or the first\\n        # update arrives. Unlike the legacy one-shot flag, this survives a\\n        # terminal/layout geometry change between the first two renders.\\n        self._startup_follow_eof = True\\n        self.follow_mode = FOLLOW_CHANGES\\n        self.tail_auto_follow = True\\n        self._snapshot_tail_pending = False\\n",
'''
if text.count(old) != 1:
    raise RuntimeError(f'constructor patch marker count={text.count(old)}')
text = text.replace(old, new, 1)

old_snapshot = '''            if reverse:\n                prior = [i for i in candidates if i < current_source]\n                target = prior[-1] if prior else candidates[-1]\n            else:\n                later = [i for i in candidates if i > current_source]\n                target = later[0] if later else candidates[0]\n'''
new_snapshot = '''            if reverse:\n                prior = [i for i in candidates if i < current_source or (self._search_last_target is None and i == current_source)]\n                target = prior[-1] if prior else candidates[-1]\n            else:\n                later = [i for i in candidates if i > current_source or (self._search_last_target is None and i == current_source)]\n                target = later[0] if later else candidates[0]\n'''
if text.count(old_snapshot) != 1:
    raise RuntimeError(f'snapshot search marker count={text.count(old_snapshot)}')
text = text.replace(old_snapshot, new_snapshot, 1)

old_history = '''        if reverse:\n            prior = [i for i in candidates if i < current]\n            target = prior[-1] if prior else candidates[-1]\n        else:\n            later = [i for i in candidates if i > current]\n            target = later[0] if later else candidates[0]\n'''
new_history = '''        if reverse:\n            prior = [i for i in candidates if i < current or (self._search_last_target is None and i == current)]\n            target = prior[-1] if prior else candidates[-1]\n        else:\n            later = [i for i in candidates if i > current or (self._search_last_target is None and i == current)]\n            target = later[0] if later else candidates[0]\n'''
if text.count(old_history) != 1:
    raise RuntimeError(f'history search marker count={text.count(old_history)}')
text = text.replace(old_history, new_history, 1)

old_normalizer = '''    'def _normalize_intentional_ui(text: str) -> str:\\n    # 0.12.0 intentionally exposes the default follow mode in pane titles.\\n    # Strip only that new label when comparing invariant behavior to v0.9.0.\\n    return text.replace(" · CHANGES", "")\\n\\n\\ndef _plain(core, rows):\\n    return [_normalize_intentional_ui(core.strip_ansi(row)) for row in rows]\\n',
'''
new_normalizer = '''    'def _normalize_intentional_ui(text: str) -> str:\\n    # 0.12.0 intentionally exposes the default follow mode in pane titles.\\n    # Remove only that label and restore the same top-border width before\\n    # comparing invariant content/render behavior to v0.9.0.\\n    marker = " · CHANGES"\\n    count = text.count(marker)\\n    if not count:\\n        return text\\n    text = text.replace(marker, "")\\n    if text.startswith("╭") and "╮" in text:\\n        end = text.rfind("╮")\\n        text = text[:end] + ("─" * (len(marker) * count)) + text[end:]\\n    return text\\n\\n\\ndef _plain(core, rows):\\n    return [_normalize_intentional_ui(core.strip_ansi(row)) for row in rows]\\n',
'''
if text.count(old_normalizer) != 1:
    raise RuntimeError(f'reference normalizer marker count={text.count(old_normalizer)}')
text = text.replace(old_normalizer, new_normalizer, 1)

path.write_text(text, encoding='utf-8')
runpy.run_path(str(path), run_name='__main__')
