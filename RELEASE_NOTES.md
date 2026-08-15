# htail 0.8.0

## New features

- Watch any number of files in one htail session, with independent scroll position, pause state, update history, idle state, and unseen-update counts for each file.
- Added live `auto`, `rows`, `columns`, `grid`, and chronological `stream` layouts. Press `l` to switch layouts without restarting or losing pane state.
- Added pane navigation with `Tab`, `Shift+Tab`, and number keys, plus `z` to maximize/restore the focused pane; the focused pane is visually distinguished.
- Added terminal mouse support: click a pane to focus it and use the mouse wheel to scroll the pane under the pointer. `--no-mouse` disables mouse tracking when preferred.
- Refactored the application into modules under `src/htail_app/` while preserving a single-file install/update artifact through the release bundler.
- Multi-file self-update reopens all watched files with the same CLI arguments after installation.

## Bug fixes

- The new bundled executable keeps self-install and self-update targeted at the real `ht` wrapper rather than the cached internal package payload.
- Pane rendering retains the existing one-column terminal safety margin, preventing content from being overwritten by the global footer after resize or layout changes.
