# htail 0.12.0

## New features

- Added per-pane **CHANGES / TAIL** follow modes. CHANGES remains the default and opens new updates at their first changed/new line; TAIL stays pinned to EOF for continuously growing program output.
- Press `t` to toggle the focused pane's follow mode. Manual upward navigation suspends TAIL auto-follow; `f`, `End`, or returning to EOF resumes it.
- Local Simple and Regex searches now show the selected `n` / `N` match with a distinct bright-yellow background, while other matches retain reverse-video highlighting.
- Pane titles now show persistent `MATCH x/y` search position and the active follow mode.

## Bug fixes

- Initial file viewing now remains bottom-aligned across startup terminal/layout geometry changes instead of consuming the EOF-position request after the first render and sometimes falling back to the top with `↓N more`.
- Search selection styling is repainted when moving between matches, so the active result never remains visually ambiguous.
