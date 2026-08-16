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
path.write_text(text.replace(old, new, 1), encoding='utf-8')
runpy.run_path(str(path), run_name='__main__')
