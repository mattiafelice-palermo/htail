# htail 0.9.0

## New features

- Read standard input as a first-class source: `producer | ht` works in the interactive TUI, with controls read from the controlling terminal. `-` can also be used explicitly alongside file sources.
- Added repeatable `--exec COMMAND` sources with merged stdout/stderr panes.
- Added `--pid PID` to exit when a producer or companion process terminates.
- Added `benchmarks/benchmark_htail.py` and `docs/NEXT.md` for reproducible performance checks and the deferred feature/performance backlog.

## Performance

- Pure same-file appends now use a byte-offset fast path instead of rereading and rediffing the complete file.
- Full rewrites now compute diff events and changed-current-row indexes in one structural pass rather than two.
- Panes cache safe Markdown rendering and ANSI-aware wrapping for unchanged lines across incremental updates.
- Active post-update content verification is rate-limited while retaining the periodic verified-snapshot fallback for unusual same-metadata rewrites.

## Bug fixes / safety

- Piped-stdin interactive sessions no longer lose keyboard controls because input is read from `/dev/tty` on POSIX.
- In-app self-update is blocked during `--exec` sessions to avoid unexpectedly relaunching a child command; use `ht --update` separately in that case.
