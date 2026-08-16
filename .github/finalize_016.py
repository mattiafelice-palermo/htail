from pathlib import Path

path = Path("src/htail_app/global_search.py")
text = path.read_text(encoding="utf-8")

if 'body_height = max(3, panel_height - 8)' not in text:
    old = '    body_height = max(3, panel_height - 7)\n'
    if text.count(old) != 1:
        raise RuntimeError("body-height patch point missing")
    text = text.replace(old, '    body_height = max(3, panel_height - 8)\n', 1)

old_off = '    off = "\\x1b[0m"\n'
new_off = '    off = "\\x1b[1;97;48;5;24m" if selected else "\\x1b[0m"\n'
if new_off not in text:
    if text.count(old_off) != 1:
        raise RuntimeError("selected-style restore patch point missing")
    text = text.replace(old_off, new_off, 1)

old_top = '    top = "╭" + "─" * (panel_width - 2) + "╮"\n'
new_top = '''    title = " Global search "\n    title_fill = max(0, panel_width - 2 - len(title))\n    top = "╭" + "─" * min(3, title_fill) + title + "─" * max(0, title_fill - 3) + "╮"\n'''
if new_top not in text:
    if text.count(old_top) != 1:
        raise RuntimeError("panel-title patch point missing")
    text = text.replace(old_top, new_top, 1)

path.write_text(text, encoding="utf-8")
