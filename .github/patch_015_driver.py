from pathlib import Path

path = Path('.github/patch_015.py')
text = path.read_text(encoding='utf-8')

old = '''# Search state type and palette state in __init__.\ntext = replace_once(\n    text,\n    '        self.prompt_restore_state: Optional[Tuple[str, str, Optional[int]]] = None\\n',\n    '        self.prompt_restore_state: Optional[Tuple[str, str, int, Optional[int]]] = None\\n',\n    "search state annotation",\n)\n'''
new = '''# Search state type is already four-part in 0.14; tolerate older staging bases.\nold_search_state = '        self.prompt_restore_state: Optional[Tuple[str, str, Optional[int]]] = None\\n'\nnew_search_state = '        self.prompt_restore_state: Optional[Tuple[str, str, int, Optional[int]]] = None\\n'\nif old_search_state in text:\n    text = text.replace(old_search_state, new_search_state, 1)\nelif new_search_state not in text:\n    raise RuntimeError("search state annotation not found")\n'''
if old not in text:
    raise RuntimeError('could not locate search-state migration block')
text = text.replace(old, new, 1)

old_help = '''text = replace_once(\n    text,\n    '            "Focused pane",\\n            "  /                  search focused pane; Tab toggles Simple / Regex",\\n',\n    '            "Focused pane",\\n            "  :                  command palette / Markdown outline",\\n            "  /                  search focused pane; Tab cycles Simple / Regex / Boolean",\\n            "  *                  search selected match / current word",\\n',\n    "help palette/search modes",\n)\n'''
new_help = '''text = replace_once(\n    text,\n    '            "Focused pane",\\n            "  /                  inline search; ↑/↓ cycle matches while typing",\\n            "  Ctrl+T             toggle Case / NoCase inside local search",\\n',\n    '            "Focused pane",\\n            "  :                  command palette / Markdown outline",\\n            "  /                  inline search; Tab cycles Simple / Regex / Boolean",\\n            "  Ctrl+T             toggle Case / NoCase inside local search",\\n            "  *                  search selected match / current word",\\n',\n    "help palette/search modes",\n)\n'''
if old_help not in text:
    raise RuntimeError('could not locate help migration block')
text = text.replace(old_help, new_help, 1)

marker = '# Frame overlay palette + status.\n'
global_patch = '''# Global search also exposes Boolean as a third mode.\ntext = replace_once(\n    text,\n    '            f"Mode: {mode_name} · Tab toggles Simple / Regex · {count_label}",\\n',\n    '            f"Mode: {mode_name} · Tab cycles Simple / Regex / Boolean · {count_label}",\\n',\n    "global search mode hint",\n)\ntext = replace_once(\n    text,\n    '        if self.global_search_mode == SEARCH_SIMPLE:\\n            content.append("Simple: literal text · * any text · ? one character")\\n        else:\\n            content.append("Regex: Python regular-expression syntax")\\n',\n    '        if self.global_search_mode == SEARCH_SIMPLE:\\n            content.append("Simple: literal text · * any text · ? one character")\\n        elif self.global_search_mode == SEARCH_REGEX:\\n            content.append("Regex: Python regular-expression syntax")\\n        else:\\n            content.append("Boolean: AND / OR / NOT, parentheses and quoted phrases")\\n',\n    "global Boolean help",\n)\ntext = replace_once(\n    text,\n    '            content.append(core.paint(f"Invalid regex: {self.global_search_error}", core.BOLD_YELLOW, self.color))\\n',\n    '            content.append(core.paint(f"Invalid search: {self.global_search_error}", core.BOLD_YELLOW, self.color))\\n',\n    "global search error wording",\n)\n'''
if marker not in text:
    raise RuntimeError('could not locate global-search insertion marker')
text = text.replace(marker, global_patch + marker, 1)

path.write_text(text, encoding='utf-8')
exec(compile(text, str(path), 'exec'), {'__name__': '__main__', '__file__': str(path)})
