# htail

`htail` is an interactive terminal file follower: a more readable, stateful alternative to `tail -f` for logs, Markdown coordination files, agent output, and other human-readable text. The installed command is intentionally short: **`ht`**.

## Quick install

Copy and run:

```bash
tmp="$(mktemp)" && curl -fL https://github.com/mattiafelice-palermo/htail/releases/latest/download/htail -o "$tmp" && chmod +x "$tmp" && "$tmp"; rm -f "$tmp"
```

On first interactive launch, htail asks whether it may install itself as `ht` in `~/.local/bin`. If `ht` already exists in `PATH`, it is never overwritten; `htail` or `hlog` is proposed instead.

You can also install explicitly:

```bash
./htail --install
./htail --install htail
```

Published releases are still one `htail` file. From 0.16 onward that file contains the application plus its runtime Python packages; on first normal launch it extracts a hash-addressed environment under `~/.cache/htail/<version>/` and reuses it afterwards. This lets htail ship compiled Python extensions while retaining the same single-file install and self-update experience. The published bundle currently targets **Linux x86-64** and uses the machine's CPython interpreter; native vendor payloads are included for CPython 3.10–3.14.

The repository-level `./htail` file is a lightweight launcher for source checkouts. `tools/build_release.py` creates the self-contained release bundle, resolving packages listed in `tools/bundle-requirements.txt` for each supported CPython ABI.

## Usage

Watch one or several files:

```bash
ht reviewer.md
ht reviewer.md implementer.md
ht --layout rows reviewer.md implementer.md
ht --layout columns reviewer.md implementer.md
ht --layout grid *.log
ht --layout stream reviewer.md implementer.md
```

Watch a dynamic glob. **Quote the pattern** so the shell does not expand it before htail sees it:

```bash
ht 'logs/*.log'
ht --glob 'logs/*.log'
ht --glob 'runs/*/agent-*.md' reviewer.md
```

Existing matches are opened immediately and new matching files are added while htail is running. `--glob` is repeatable. Overlapping patterns are deduplicated. Recursive `**` patterns are supported; a periodic scan remains as a safety net when a native directory notification cannot cover a newly created nested path immediately.

Watch a pipe or run a command directly:

```bash
pytest -q | ht
cat build.log | ht -
ht --exec "pytest -vv"
ht server.log --exec "python worker.py"
ht --pid 12345 server.log
```

When stdin is a pipe, htail reads keyboard/mouse controls from the controlling terminal, so the full-screen UI remains interactive. `--exec` is repeatable and merges the child command's stderr into its stdout pane.

Interactive htail reads the **full current file** and initially positions each pane at EOF: if the file fits, the whole file is visible; otherwise the pane shows the final screenful after wrapping. `-n` is retained only for non-interactive tail-like output.

### Multi-file layouts

- `auto` — chooses rows/columns/grid from file count and terminal geometry.
- `rows` — vertical stacking.
- `columns` — horizontal stacking.
- `grid` — automatic N×M pane composition.
- `stream` — one chronological feed with every update labelled by source file.

Press `l` while htail is running to switch layout without restarting. Pane scroll positions, pause state, captured history, and unseen-update counts are preserved.

### Interactive controls

| Key / input | Action |
|---|---|
| `/` | Open the focused pane's inline live search field |
| `↑` / `↓` while searching | Previous / next live match |
| `Ctrl+T` while searching | Toggle Case / NoCase matching |
| `n` / `N` | Jump to next / previous committed search match, wrapping at the ends |
| `h` | Enter a persistent regex highlight for the focused pane |
| `H` | Clear the focused pane's regex highlight |
| `Tab` / `Shift+Tab` | Focus next / previous pane |
| `1`–`9` | Focus a pane directly |
| mouse click | Focus the pane under the pointer |
| mouse wheel | Scroll the pane under the pointer |
| `l` | Open the live layout chooser |
| `z` | Maximize focused pane / restore layout |
| `↑` / `↓` | Scroll focused pane one visual row |
| `PgUp` / `PgDn` | Scroll focused pane one page |
| `Home` / `End` | First line / bottom of focused pane |
| `[` / `]` | Previous / next captured update in focused pane |
| `f` | Jump focused pane to its freshest update |
| `p` | Pause/resume automatic jumps in focused pane |
| `t` | Toggle focused pane between **CHANGES** and **TAIL** follow modes |
| `c` | Clear focused pane history without resetting file tracking |
| `u` | Check GitHub now; if an update exists, open its modal automatically |
| `?` | Toggle help |
| `q` | Quit |

Simple search is the default; `Tab` cycles the inline field through **Simple**, **Regex** and **Boolean**. `-I` / `--ignore-case` sets the initial case behavior, and `Ctrl+T` toggles Case / NoCase interactively. Matches highlight live while you type: the first match is selected immediately, `↑` / `↓` cycle through results without closing the editor, and the selected match uses high-contrast black-on-orange. Match progress appears as a prominent `x/y MATCHES` badge inside the pane. Persistent regex highlights use underline so existing syntax colors remain visible.

Mouse tracking can be disabled with `--no-mouse`. Keyboard controls always remain available.

### Search

Press `/` for search inside the focused pane. A compact search field attaches to the bottom of that pane instead of opening a modal, so matching text remains visible and updates live while you type. Search opens in **Simple** mode: ordinary text is literal, `*` means any text and `?` means one character. The first match is selected immediately; use `↑` / `↓` to cycle results while still editing. Press `Tab` to cycle **Simple → Regex → Boolean**, `Ctrl+T` to toggle Case / NoCase, `Enter` to commit, or `Esc` to restore the previous search and close the editor. After applying a search, `n` / `N` move between matches.

Examples:

```text
045blabla       literal substring
045*blabla      045, then any text, then blabla
run-??-error    exactly two characters between the dashes
```

Press `g` for **global live search** across every currently watched file. The 0.16 interface is a structured search workspace: query/mode/filter controls at the top, results on the left, and surrounding source context on the right when terminal width permits. The preview disappears automatically on narrow terminals rather than compressing both columns.

`Tab` cycles **Simple → Regex → Boolean → Fuzzy**. Simple, Regex and Boolean default to **File** organization. Fuzzy uses bundled RapidFuzz C++ scoring and defaults to a flat global **Relevance** ranking, so the best result can come from any watched file. In Fuzzy + File mode, file groups are ordered by their best score and the selected file group expands while the others remain compact. `Ctrl+O` toggles Relevance/File ordering in Fuzzy mode, `Ctrl+F` cycles the file filter, `Ctrl+T` toggles Case/NoCase, and `Ctrl+P` shows/hides the context preview. `↑` / `↓` navigate one continuous result sequence and `Enter` focuses the source pane and jumps to the selected line.

Every pane starts at **EOF** on first open. In the default **CHANGES** follow mode, a new update moves only its own pane to the first changed/new line. Press `t` to switch that pane to **TAIL** mode, where updates keep the viewport at EOF like `tail -f`. Manually scrolling upward in TAIL mode suspends auto-follow so the viewport is not yanked away; `f`, `End`, or scrolling back to EOF resumes it. Other panes keep their current reading position. While a pane is paused, changes are still captured and its title reports unseen updates.

## Display and performance features

- Timestamped and numbered update batches per file.
- Highlighted new/replacement content with a persistent change gutter across wrapped rows.
- Rendered Markdown headings, emphasis, lists, links, rules, and fenced code.
- Optional Pygments syntax highlighting for code and fenced blocks.
- Soft wrapping with hanging indents for bullets, task lists, and numbered lists.
- Per-pane pause, scrolling, search, regex highlighting, update navigation, idle state, and unseen-update counts.
- Display-only `--grep` / `--exclude` filters; hidden lines remain in change tracking.
- Robust following across append, truncation, rewrite, atomic replacement, staged writes, and same-size rewrites.
- Fast append-only path: ordinary growing logs are consumed from the previous byte offset instead of rereading the complete file on every append.
- Incremental Markdown render/wrap caches reuse unchanged visual work across small updates.
- Native filesystem wakeups on Linux and Windows avoid redundant idle metadata probes; periodic verified snapshots remain the correctness fallback.
- Damage rendering retains the previous terminal frame and writes only physical rows whose final content changed.

Use `--no-native-watch` to force the older polling scheduler. This is primarily a troubleshooting/benchmark option; file-change semantics still use the same verified follower logic in either mode.

## Updates

htail checks GitHub on startup and once per hour during long sessions. Press `u` to force an immediate check. If a newer version is found, the confirmation/changelog modal opens as soon as the check completes—there is no second `u` press.

After confirmation htail downloads the release asset with a progress bar, verifies SHA-256, keeps a `.bak` copy, atomically replaces itself, and reopens **all watched files with the same command-line options**. `ht --update` uses the same progress reporting.

```bash
ht --check-update
ht --update
```

No update is installed without confirmation.

## Useful options

```bash
ht -n 20 file.md  # non-interactive initial context override
ht --glob 'logs/*.log'
ht --verify-interval 0.5 file.md
ht --idle-warn 120 file.md
ht --grep 'REVIEW|IMPLEMENTER' *.md
ht --exclude 'DEBUG' *.log
ht --syntax markdown file.txt
ht --syntax none file.md
ht --show-deletions file.md
ht --no-mouse a.log b.log
ht --no-native-watch a.log b.log
```

## Development

The repository source is split into modules under `src/htail_app/`, while releases remain a **single executable text file** for curl installation and atomic self-update. `tools/build_release.py` packages the source into the standalone `htail` wrapper.

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python tools/build_release.py --output /tmp/htail
python /tmp/htail --version
```

## Benchmarks and behavior reference

The synthetic append/diff benchmark remains available:

```bash
PYTHONPATH=src python benchmarks/benchmark_htail.py
PYTHONPATH=src python benchmarks/benchmark_htail.py --sizes 1 10 50 100 --iterations 3
```

For optimization work, `v0.9.0` is also frozen as a differential behavior reference:

```bash
PYTHONPATH=src python benchmarks/reference_compare.py --reference v0.9.0
```

The reference probe runs the same deterministic file/diff/pane/terminal scenarios against the published tag and the candidate. It requires exact equality for the invariant application state and content-area terminal result; intentionally changed release/footer controls are outside that invariant. It also reports same-machine performance ratios for idle file polling and terminal redraw traffic. Absolute timings depend on storage and VM hardware, so ratios from the same machine are the useful metric.

See `docs/NEXT.md` for deliberately deferred ideas.


### 0.15 inspection and source features

Press `:` for the command palette. It includes a Markdown heading outline, per-pane wrap and line-number toggles, heartbeat configuration, follow mode, search clearing, search-selected/current-word, and pane switching. The outline jumps directly to headings in the current Markdown snapshot.

Search now has three modes: **Simple**, **Regex**, and **Boolean**. `Tab` cycles them. Boolean mode accepts `AND`, `OR`, `NOT`, parentheses, quoted phrases, and implicit AND; individual terms use the same friendly wildcard semantics as Simple search. `*` searches the active selected match, or a useful token at the current viewport when there is no selected match.

Wrap is per pane. With wrap disabled, `←` / `→` scroll horizontally and the pane title reports the horizontal offset. Line numbers are also per pane and can be toggled from the command palette.

`--heartbeat 5m` sets an expected update interval. A source that exceeds it is marked `LATE`; per-pane heartbeat can be cycled from the command palette. Active sources also show a rolling line/byte rate. `--exec` and `--ssh` panes display PID, runtime, and exit status.

Compressed `.gz`, `.bz2`, `.xz`, and `.lzma` files can be opened directly as **static** sources. Remote files can be followed with the system OpenSSH client using `--ssh user@host:/path` or `--ssh ssh://user@host/path`; existing SSH config, keys, agents, ProxyJump, and host-key policy remain owned by OpenSSH.

Visible `http://` and `https://` URLs are emitted as OSC-8 terminal hyperlinks, so supporting terminals can open them directly (typically Ctrl/Cmd-click depending on the terminal).
