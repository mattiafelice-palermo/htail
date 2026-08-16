from pathlib import Path

path = Path("README.md")
text = path.read_text(encoding="utf-8")


def replace_once(old, new, label):
    global text
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    """```bash\n./htail --install\n./htail --install htail\n```\n\n## Usage\n""",
    """```bash\n./htail --install\n./htail --install htail\n```\n\nPublished releases are still one `htail` file. From 0.16 onward that file contains the application plus its runtime Python packages; on first normal launch it extracts a hash-addressed environment under `~/.cache/htail/<version>/` and reuses it afterwards. This lets htail ship compiled Python extensions while retaining the same single-file install and self-update experience. The published bundle currently targets **Linux x86-64** and uses the machine's CPython interpreter; native vendor payloads are included for CPython 3.10–3.14.\n\nThe repository-level `./htail` file is a lightweight launcher for source checkouts. `tools/build_release.py` creates the self-contained release bundle, resolving packages listed in `tools/bundle-requirements.txt` for each supported CPython ABI.\n\n## Usage\n""",
    "bundle install docs",
)

replace_once(
    """Simple search is the default; `Tab` switches the inline field to explicit regex mode. `-I` / `--ignore-case` sets the initial case behavior, and `Ctrl+T` toggles Case / NoCase interactively. Matches highlight live while you type: the first match is selected immediately, `↑` / `↓` cycle through results without closing the editor, and the selected match uses high-contrast black-on-orange. Match progress appears as a prominent `x/y MATCHES` badge inside the pane. Persistent regex highlights use underline so existing syntax colors remain visible.\n""",
    """Simple search is the default; `Tab` cycles the inline field through **Simple**, **Regex** and **Boolean**. `-I` / `--ignore-case` sets the initial case behavior, and `Ctrl+T` toggles Case / NoCase interactively. Matches highlight live while you type: the first match is selected immediately, `↑` / `↓` cycle through results without closing the editor, and the selected match uses high-contrast black-on-orange. Match progress appears as a prominent `x/y MATCHES` badge inside the pane. Persistent regex highlights use underline so existing syntax colors remain visible.\n""",
    "local mode docs",
)

replace_once(
    """Press `/` for search inside the focused pane. A compact search field attaches to the bottom of that pane instead of opening a modal, so matching text remains visible and updates live while you type. Search opens in **Simple** mode: ordinary text is literal, `*` means any text and `?` means one character. The first match is selected immediately; use `↑` / `↓` to cycle results while still editing. Press `Tab` to switch to explicit Python-regex mode, `Ctrl+T` to toggle Case / NoCase, `Enter` to commit, or `Esc` to restore the previous search and close the editor. After applying a search, `n` / `N` move between matches.\n""",
    """Press `/` for search inside the focused pane. A compact search field attaches to the bottom of that pane instead of opening a modal, so matching text remains visible and updates live while you type. Search opens in **Simple** mode: ordinary text is literal, `*` means any text and `?` means one character. The first match is selected immediately; use `↑` / `↓` to cycle results while still editing. Press `Tab` to cycle **Simple → Regex → Boolean**, `Ctrl+T` to toggle Case / NoCase, `Enter` to commit, or `Esc` to restore the previous search and close the editor. After applying a search, `n` / `N` move between matches.\n""",
    "local search section",
)

replace_once(
    """Press `g` for **global live search** across every currently watched file. Results update as you type. Use `↑` / `↓` to choose a result and `Enter` to focus its pane and jump to the matching source line; the query becomes that pane's active local search. `Tab` toggles Simple / Regex here as well.\n""",
    """Press `g` for **global live search** across every currently watched file. The 0.16 interface is a structured search workspace: query/mode/filter controls at the top, results on the left, and surrounding source context on the right when terminal width permits. The preview disappears automatically on narrow terminals rather than compressing both columns.\n\n`Tab` cycles **Simple → Regex → Boolean → Fuzzy**. Simple, Regex and Boolean default to **File** organization. Fuzzy uses bundled RapidFuzz C++ scoring and defaults to a flat global **Relevance** ranking, so the best result can come from any watched file. In Fuzzy + File mode, file groups are ordered by their best score and the selected file group expands while the others remain compact. `Ctrl+O` toggles Relevance/File ordering in Fuzzy mode, `Ctrl+F` cycles the file filter, `Ctrl+T` toggles Case/NoCase, and `Ctrl+P` shows/hides the context preview. `↑` / `↓` navigate one continuous result sequence and `Enter` focuses the source pane and jumps to the selected line.\n""",
    "global search docs",
)

path.write_text(text, encoding="utf-8")
