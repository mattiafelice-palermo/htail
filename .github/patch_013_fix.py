from pathlib import Path
import runpy

path = Path('.github/patch_013.py')
text = path.read_text(encoding='utf-8')
old = '''test12 = replace_once(\n    test12,\n    '        self.assertIn("MATCH 1/3", core.strip_ansi(pane.title(0, 100, True, 4)))\\n',\n    '        self.assertIn("MATCH 1/3", core.strip_ansi(pane.render_box(100, 6, True, 0)[0]))\\n',\n    "0.12 match 1 badge expectation",\n)\n'''
new = '''old_match_one = '        self.assertIn("MATCH 1/3", core.strip_ansi(pane.title(0, 100, True, 4)))\\n'\nnew_match_one = '        self.assertIn("MATCH 1/3", core.strip_ansi(pane.render_box(100, 6, True, 0)[0]))\\n'\nif test12.count(old_match_one) != 2:\n    raise RuntimeError(f"0.12 MATCH 1/3 migration count={test12.count(old_match_one)}")\ntest12 = test12.replace(old_match_one, new_match_one)\n'''
if text.count(old) != 1:
    raise RuntimeError(f'patch migration marker count={text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
runpy.run_path(str(path), run_name='__main__')
