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

## Usage

Watch one file:

```bash
ht reviewer.md
```

Watch several files at once:

```bash
ht reviewer.md implementer.md
ht --layout rows reviewer.md implementer.md
ht --layout columns reviewer.md implementer.md
ht --layout grid *.log
ht --layout stream reviewer.md implementer.md
```

`-n` controls **only the initial context per file**. Once htail is running, every observed change is retained.

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
| `c` | Clear focused pane history without resetting file tracking |
| `u` | Check GitHub now; if available, show update confirmation/changelog |
| `?` | Toggle help |
| `q` | Quit |

Mouse tracking can be disabled with `--no-mouse`. Keyboard controls always remain available.

A new update moves **only its own pane** to the beginning of that update. Other panes keep their current reading position. While a pane is paused, changes are still captured and its title reports unseen updates.

## Display features

- Timestamped and numbered update batches per file.
- Highlighted new/replacement content with a persistent change gutter across wrapped rows.
- Rendered Markdown headings, emphasis, lists, links, rules, and fenced code.
- Optional Pygments syntax highlighting for code and fenced blocks.
- Soft wrapping with hanging indents for bullets, task lists, and numbered lists.
- Per-pane pause, scrolling, update navigation, idle state, and unseen-update counts.
- Display-only `--grep` / `--exclude` filters; hidden lines remain in change tracking.
- Robust following across append, truncation, rewrite, atomic replacement, staged writes, and same-size rewrites.

## Updates

htail checks GitHub on startup and once per hour during long sessions. Press `u` to force an immediate check. The in-app update panel shows release notes split into **New features** and **Bug fixes**.

After confirmation htail downloads the release asset and checksum, verifies SHA-256, keeps a `.bak` copy, atomically replaces itself, and reopens **all watched files with the same command-line options**.

```bash
ht --check-update
ht --update
```

No update is installed without confirmation.

## Useful options

```bash
ht -n 0 file.md
ht --verify-interval 0.5 file.md
ht --idle-warn 120 file.md
ht --grep 'REVIEW|IMPLEMENTER' *.md
ht --exclude 'DEBUG' *.log
ht --syntax markdown file.txt
ht --syntax none file.md
ht --show-deletions file.md
ht --no-mouse a.log b.log
```

## Development

The repository source is split into modules under `src/htail_app/`, while releases remain a **single executable text file** for curl installation and atomic self-update. `tools/build_release.py` packages the source into the standalone `htail` wrapper.

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python tools/build_release.py --output /tmp/htail
python /tmp/htail --version
```
