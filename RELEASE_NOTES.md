# htail 0.7.3

## New features

- Redesigned the in-app update confirmation as a centered terminal panel with clearer actions and version information.
- The update panel now shows the GitHub release changelog directly inside htail, separated into New features and Bug fixes.
- The footer now advertises `u check` even when no update is pending; pressing `u` forces an immediate GitHub release check.

## Bug fixes

- Fixed a terminal row-accounting bug where content could disappear underneath the two-line footer while scrolling. htail now deliberately leaves the terminal's final physical column unused, preventing implicit terminal wrapping from consuming an extra row.
