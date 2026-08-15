# htail 0.10.0

## New features

- Added interactive regex search with `/`, plus `n` / `N` for next/previous match navigation in the focused pane.
- Added persistent regex highlighting with `h`; press `H` to clear the focused pane's highlight rule.
- Added dynamic glob sources. Quote positional patterns such as `ht 'logs/*.log'` or use repeatable `--glob PATTERN`; new matching files are added without restarting htail.
- Pressing `u` now performs a manual update check and opens the update modal automatically as soon as a newer release is found; a second `u` is no longer required.
- Added a permanent v0.9.0 differential reference harness so behavior and performance can be compared on the same machine before accepting future optimizations.

## Performance

- Added terminal damage rendering: htail keeps the previous rendered frame and rewrites only physical terminal rows whose final content changed.
- Added native filesystem wakeups on Linux (inotify) and Windows directory-change notifications, with the existing polling/verified-snapshot path retained as a portable correctness fallback.
- Native notifications are scheduling hints only: the existing debounce, append fast path, rewrite detection and periodic verified snapshot remain the authority for file semantics.

## Bug fixes / safety

- Manual update checks no longer stop at the transient “update available — press u” message; the confirmation modal opens immediately when the check completes.
- Regex styling uses attribute-specific ANSI on/off codes so search/highlight overlays do not reset existing syntax-highlight foreground or bold styles.
