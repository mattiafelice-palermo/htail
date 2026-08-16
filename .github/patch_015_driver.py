from pathlib import Path

path = Path('.github/patch_015.py')
text = path.read_text(encoding='utf-8')
old = '''# Search state type and palette state in __init__.\ntext = replace_once(\n    text,\n    '        self.prompt_restore_state: Optional[Tuple[str, str, Optional[int]]] = None\\n',\n    '        self.prompt_restore_state: Optional[Tuple[str, str, int, Optional[int]]] = None\\n',\n    "search state annotation",\n)\n'''
new = '''# Search state type is already four-part in 0.14; tolerate older staging bases.\nold_search_state = '        self.prompt_restore_state: Optional[Tuple[str, str, Optional[int]]] = None\\n'\nnew_search_state = '        self.prompt_restore_state: Optional[Tuple[str, str, int, Optional[int]]] = None\\n'\nif old_search_state in text:\n    text = text.replace(old_search_state, new_search_state, 1)\nelif new_search_state not in text:\n    raise RuntimeError("search state annotation not found")\n'''
if old not in text:
    raise RuntimeError('could not locate search-state migration block')
text = text.replace(old, new, 1)
path.write_text(text, encoding='utf-8')
exec(compile(text, str(path), 'exec'), {'__name__': '__main__', '__file__': str(path)})
