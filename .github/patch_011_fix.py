from pathlib import Path
import runpy

path = Path('.github/patch_011.py')
text = path.read_text(encoding='utf-8')
old = 'new, count = re.subn(pattern, replacement, text, count=1, flags=re.S)'
new = 'new, count = re.subn(pattern, lambda _match: replacement, text, count=1, flags=re.S)'
if old not in text:
    raise RuntimeError('sub_once implementation marker missing')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
runpy.run_path(str(path), run_name='__main__')
