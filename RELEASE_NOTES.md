# htail 0.16.0

## New features

- Redesigned global search as a structured two-pane TUI: search controls and ranked/grouped results on the left, source context preview on the right. Narrow terminals automatically drop the preview rather than squeezing both panes.
- Added **Fuzzy** as a fourth global-search mode alongside Simple, Regex and Boolean.
- Fuzzy results use bundled RapidFuzz C++ scoring and default to a flat global **Relevance** ranking across all files.
- Fuzzy search can switch to **File** organization; file groups are ordered by their best score and the selected file expands while the others stay compact.
- Added interactive global-search controls for Case/NoCase (`Ctrl+T`), Relevance/File ordering (`Ctrl+O`), file filtering (`Ctrl+F`) and preview visibility (`Ctrl+P`).
- Global search now caches its candidate corpus and search state so unchanged files are not rebuilt on every redraw.

## Distribution

- Replaced the release zipapp payload with an extracted, hash-addressed application environment under the htail cache. Native Python extensions can now be bundled and imported normally.
- Added a generic `tools/bundle-requirements.txt` dependency manifest. The release builder resolves wheel dependencies for CPython 3.10–3.14 and embeds a matching vendor directory for each ABI.
- RapidFuzz 3.14.5 is the first bundled native dependency. The same mechanism can support future dependencies such as NumPy without another packaging redesign.
- Published release bundles currently target Linux x86-64 and continue to use the system Python interpreter.
- The repository-level `./htail` is now a lightweight source-checkout launcher; release assets remain the self-contained install/update artifact.
