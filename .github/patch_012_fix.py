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

path.write_text(text, encoding='utf-8')
runpy.run_path(str(path), run_name='__main__')
