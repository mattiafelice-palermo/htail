# htail 0.14.0

## New features

- Local search now selects the first live match immediately while typing, with `↑` / `↓` cycling matches without leaving the inline editor.
- Match progress is shown as a prominent status badge inside the pane (`1/4 MATCHES`) instead of being embedded in the top border.
- `Ctrl+T` toggles Case / NoCase search interactively; the search row always shows the current state and the available shortcut.

## Bug fixes

- Fixed `Esc` handling on native Windows so the inline search reliably closes there as it already did on POSIX terminals.
- Selected matches now use black-on-orange high-contrast styling rather than relying on syntax foreground colours.
- Search viewport geometry now accounts for both the internal match-status row and the inline editor row.
