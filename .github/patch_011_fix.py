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

# Preserve the established navigation rule: n searches after the current
# source row. Test Simple-vs-Regex matching directly rather than assuming the
# first matching row is selected when it is already at the viewport top.
test_path = Path('tests/test_search_011.py')
test = test_path.read_text(encoding='utf-8')
old_test = '''        self.assertIsNone(pane.set_search("a.b", mode=SEARCH_SIMPLE))\n        self.assertEqual(pane.search_mode, SEARCH_SIMPLE)\n        self.assertTrue(pane.search_next(False, 40, 3))\n        self.assertEqual(pane._search_last_target, 0)\n\n        self.assertIsNone(pane.set_search("a.b", mode=SEARCH_REGEX))\n        self.assertTrue(pane.search_next(False, 40, 3))\n        self.assertEqual(pane._search_last_target, 0)\n        self.assertTrue(pane.search_next(False, 40, 3))\n        self.assertEqual(pane._search_last_target, 1)\n'''
new_test = '''        self.assertIsNone(pane.set_search("a.b", mode=SEARCH_SIMPLE))\n        self.assertEqual(pane.search_mode, SEARCH_SIMPLE)\n        self.assertIsNotNone(pane.search_regex.search("a.b literal"))\n        self.assertIsNone(pane.search_regex.search("axb regex-like"))\n\n        self.assertIsNone(pane.set_search("a.b", mode=SEARCH_REGEX))\n        self.assertIsNotNone(pane.search_regex.search("a.b literal"))\n        self.assertIsNotNone(pane.search_regex.search("axb regex-like"))\n'''
if old_test not in test:
    raise RuntimeError('search mode regression block missing')
test_path.write_text(test.replace(old_test, new_test, 1), encoding='utf-8')
