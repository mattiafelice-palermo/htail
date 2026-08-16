# htail 0.16.2

## Bug fixes

- Fixed the global-search grouped-results renderer crashing on the first matching query in Simple, Regex and Boolean modes. Group members are `(index, result)` pairs; the renderer now reads fuzzy score metadata from the contained result rather than from the tuple itself.
- Strengthened the previous global-search containment regression so an error fallback no longer counts as a successful render.

## Validation

- Added an end-to-end live-render matrix covering Simple, Regex, Boolean and Fuzzy search in both color and no-color modes.
- The matrix renders the complete TUI after every typed character and after selection, case, file-filter, preview and fuzzy sort controls, and explicitly rejects any search-rendering error panel.
- Existing full test suite, v0.9.0 differential behavior gate, standalone core build and current-ABI native-runtime smoke test remain required before release.
