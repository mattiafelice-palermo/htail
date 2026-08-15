# htail

`htail` is an interactive terminal file follower: a more readable, stateful alternative to `tail -f` for logs, Markdown coordination files, agent output, and other human-readable text.

The installed command is intentionally short: **`ht`**.

## Quick install

Copy and run this command:

```bash
tmp="$(mktemp)" && curl -fsSL https://raw.githubusercontent.com/mattiafelice-palermo/htail/main/htail -o "$tmp" && chmod +x "$tmp" && "$tmp"; rm -f "$tmp"
```

On first interactive launch, htail asks whether it may install itself as `ht` in `~/.local/bin`. If `ht` already exists in your `PATH`, it will **not overwrite it** and will offer `htail` or `hlog` instead.

If `~/.local/bin` is not already in `PATH`, htail adds it to the appropriate Bash, Zsh, or Fish startup configuration when possible. A new shell may be required before `ht` is available by name.

You can also install explicitly:

```bash
./htail --install
# or choose another command name
./htail --install htail
```

## Usage

```bash
ht file.md
ht -n 100 logfile.txt
ht -n 0 agent-output.md
```

`-n` controls **only the initial context**. Once htail is running, every observed change is retained; a 500-line update is not truncated to the initial line count.

### Interactive controls

| Key | Action |
|---|---|
| `↑` / `↓` | Scroll one visual row |
| `PgUp` / `PgDn` | Scroll one page |
| `Home` / `End` | First line / bottom |
| `[` / `]` | Previous / next captured update |
| `f` | Jump to the beginning of the freshest update |
| `p` | Pause/resume automatic jumps; file changes are still captured |
| `c` | Clear displayed history without resetting file tracking |
| `u` | Open the update confirmation dialog when a release is available |
| `?` | Toggle help |
| `q` | Quit |

New updates open at their **first line**, not at their end. While paused, htail keeps collecting updates but does not move the viewport.

## Display features

- Timestamped and numbered update batches.
- New/replacement content highlighted without destroying syntax colours.
- Rendered Markdown headings, emphasis, lists, links, rules, and fenced code.
- Optional Pygments syntax highlighting for Python, JSON, YAML, TOML, shell, and other formats.
- Automatic Pygments installation prompt when richer highlighting is useful and Pygments is missing.
- Soft wrapping to terminal width.
- **Hanging indents** for wrapped bullets, task-list items, and numbered lists.
- Idle-time status and configurable idle warning.
- Display-only `--grep` / `--exclude` filters; hidden lines remain part of internal change tracking.
- Robust following across append, truncation, rewrite, atomic replacement, staged writes, and same-size rewrites.

## Updates

htail checks the latest GitHub Release in the background while the interactive viewer is open. If a newer release is available, the footer shows an update indicator.

Press `u` to open a confirmation dialog. If confirmed, htail:

1. downloads the `htail` release asset;
2. downloads and verifies `htail.sha256`;
3. validates the downloaded Python source and embedded version;
4. keeps a `.bak` copy of the current executable;
5. atomically replaces the current executable; and
6. reopens the **same file with the same command-line options**.

Command-line update checks are also available:

```bash
ht --check-update
ht --update
```

No update is installed automatically without confirmation.

## Useful options

```bash
# Only show future changes
ht -n 0 file.md

# Faster content verification
ht --verify-interval 0.5 file.md

# Warn after two minutes without changes
ht --idle-warn 120 file.md

# Display filters (tracking remains complete)
ht --grep 'REVIEW|IMPLEMENTER' file.md
ht --exclude 'DEBUG' file.md

# Explicit syntax selection / disable rendering
ht --syntax markdown file.txt
ht --syntax python script.txt
ht --syntax none file.md

# Show removed lines as well
ht --show-deletions file.md
```

## Development and releases

The project is intentionally a single executable Python script with no required third-party dependency. Tests use the standard library `unittest` runner:

```bash
python -m unittest discover -s tests -v
```

Releases are created from tags such as `v0.7.0`. The release workflow verifies that the tag matches `HTAIL_VERSION`, runs the tests, and publishes both `htail` and `htail.sha256`.
