# htail 0.13.0

## New features

- Local `/` search now uses an inline field attached to the bottom of the focused pane instead of a modal, keeping file content visible while searching.
- Simple and Regex matches highlight live as the query is typed; `Tab` switches mode, `Enter` commits, and `Esc` restores the previous search.
- Match progress now appears as a dedicated high-contrast badge on the pane's top-right border instead of competing with filename/follow/scroll state in the left title.

## Bug fixes

- Selected `n` / `N` matches now use guaranteed black-on-bright-yellow rendering so syntax-highlighted white text cannot produce an unreadable white/yellow combination.
- Inline search reserves a real pane row, so opening the editor does not cover the final content line or invalidate EOF/scroll indicators.
