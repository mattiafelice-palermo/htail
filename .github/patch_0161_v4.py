from pathlib import Path
import runpy

runpy.run_path('.github/patch_0161_v3.py', run_name='__main__')

path = Path('tests/test_regressions_084.py')
text = path.read_text(encoding='utf-8')
old = "self.assertIn('Verifying SHA-256 checksum…', stages)"
new = "self.assertIn('Verifying release SHA-256 checksum…', stages)"
if text.count(old) != 1:
    raise RuntimeError('update progress regression expectation not found exactly once')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
