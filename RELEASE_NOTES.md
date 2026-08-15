# htail 0.8.4

## New features

- Interactive startup is geometry-based: panes read the full file and open at EOF, showing the whole file when it fits or the final screenful after wrapping when it does not.
- Pane titles show visual rows above/below the viewport (`↑N` / `↓N`) whenever more retained content exists.
- `ht --update` now shows staged download progress just like the in-app updater.

## Bug fixes

- Fixed POSIX/WSL mouse input by reading escape sequences directly from the terminal file descriptor instead of Python's buffered text stream.
- Restored byte-level progress updates in the interactive updater while preserving checksum verification, backup, atomic replacement and restart.
- Removed the legacy 50-source-line cap from interactive mode; `-n` remains accepted for non-interactive compatibility only.
