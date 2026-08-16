from pathlib import Path
import runpy

path = Path('.github/patch_014.py')
text = path.read_text(encoding='utf-8')
old = '''text = replace_once(\n    text,\n    '            return "TAB" if ch == "\\\\t" else ch\\n',\n    '            return normalize_plain_key(ch)\\n',\n    "windows plain key normalization",\n)\n'''
new = '''old_plain_return = '            return "TAB" if ch == "\\\\t" else ch\\n'\nif text.count(old_plain_return) != 2:\n    raise RuntimeError(f"plain-key return count={text.count(old_plain_return)}")\ntext = text.replace(old_plain_return, '            return normalize_plain_key(ch)\\n', 1)\n'''
if text.count(old) != 1:
    raise RuntimeError(f'windows migration marker count={text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
runpy.run_path(str(path), run_name='__main__')
