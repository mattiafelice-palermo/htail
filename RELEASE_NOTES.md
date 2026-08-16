# htail 0.16.5

## Fuzzy search relevance

- Fuzzy global search now scores the best substring alignment in each line instead of using whole-line `WRatio`, so an exact query occurrence scores 100 regardless of surrounding line length.
- Fuzzy match highlighting now uses RapidFuzz's winning partial-ratio alignment, keeping the highlighted fragment consistent with the score.

## Regression coverage

- Added coverage for a long line containing an exact `reviewer` occurrence outranking a shorter approximate `review` match, including case-insensitive alignment.

# htail 0.16.4

## Global search UX

- Fuzzy relevance results now have explicit `#`, `FILE`, `LINE`, `MATCH`, and `SCORE` column headers, with scores aligned in a fixed right-hand column.
- Grouped results now have real file expansion state. Click a file header to expand/collapse it; clicking a result selects it.
- Shift+Up / Shift+Down jumps directly between files with matches, while Up / Down continues to move match-by-match.
- Global-search mouse wheel navigation is supported inside the results list.
- The sort shortcut is now displayed as `Ctrl+O(letter)` / `Ctrl+O (letter O)` so it cannot be confused with terminal `Ctrl+0` zoom/reset shortcuts.

## Regression coverage

- Added renderer, shifted-key decoding, file-jump, expansion/collapse, and mouse hit-target tests.

# htail 0.16.3

## Update reliability

- Hardened application selection after self-update. The release launcher now explicitly loads the application embedded in the current wrapper and discards any stale `htail_app` package that may have been preloaded by an inherited Python environment.
- Extracted application caches are validated against the wrapper version and runtime manifest and are automatically rebuilt if stale or incomplete.
- The updater now verifies both the installed wrapper version and the installed application with `--bundle-self-test` before declaring success. Failed verification restores the previous executable from backup.
- Bundle self-test now checks wrapper/application version agreement and the active application path in addition to the RapidFuzz runtime.

## Regression coverage

- Added a hostile inherited-environment test that preloads a fake stale `htail_app` through `sitecustomize`; the bundled launcher must still select the current application.
- Added extracted-cache repair and updater rollback coverage.
- The full-frame global-search acceptance matrix introduced in 0.16.2 remains part of the release gate.
