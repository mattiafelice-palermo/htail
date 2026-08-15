# htail 0.8.5

## New features

- Latest updates are now shown inside a scrollable full-current-file snapshot. The update marker is anchored at the first changed region and changed rows retain the cyan gutter; `[` / `]` remain the explicit controls for historical update records.
- Pane bottom borders now show a prominent `↓N more` indicator whenever visual rows remain below the viewport, in addition to the compact title counters.
- The in-app updater now keeps one overall progress bar visible across download, checksum verification, backup and installation, and briefly displays 100% before restarting.

## Bug fixes

- Held-arrow scrolling is substantially more responsive because keyboard/mouse handling and terminal rendering now run at roughly 60 Hz independently of the file polling interval.
- Scrolling is clamped at the last full viewport, so the pane can no longer move past EOF into blank space.
- Scrolling around the latest update no longer falls back into the old initial-file plus diff-fragment history.
