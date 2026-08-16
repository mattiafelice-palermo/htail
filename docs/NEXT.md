# htail — possible next work

This file records useful ideas that are **not part of the current release**. It is intentionally a backlog, not a commitment.

## Navigation and inspection

- **Bookmarks / marks** — mark interesting rows, jump between marks, and optionally copy/export marked rows. Inspired by lnav's bookmark workflow.
- **Semantic log-level navigation** — optional detection of ERROR/WARN/etc. with next/previous-error shortcuts. Keep this generic rather than building a full log-schema engine.
- **Session restore** — remember layout, pane focus, scroll positions, filters, search and highlights for the same source set.
- **Search-result overview** — now that interactive regex search exists, matches could also appear on a compact scrollbar/minimap.
- **Global live search palette** — open a modal that searches across all currently watched files while the user types, showing source/pane plus a short matching-line preview. Selecting a result should close the modal, focus the corresponding pane, jump to that match, and temporarily highlight the matched text. The interaction should support an easy literal/wildcard mode as well as explicit regex mode rather than requiring regex syntax for ordinary searches.
- **Multiple named highlight rules** — 0.10.0 provides one persistent regex highlight per pane; a future version could support several independently styled rules.

## Sources

- **Structured JSON view** — pretty-print JSON/JSONL with optional collapse/expand while preserving the raw source for change tracking.
- **Open/add source from inside the TUI** — an `:open`-style command palette rather than restarting htail.
- **Richer dynamic-source policy** — optional automatic pane removal/archival for glob-matched files that permanently disappear.

## UI ideas

- **Scrollbar/minimap** — proportional position plus marks for updates, search hits, warnings and bookmarks.
- **Cursor/selection mode** — select/copy lines without fighting terminal mouse selection; potentially expose context actions for search/filter/highlight.
- **Custom key maps / themes** — configurable bindings and a small theme layer while retaining a sane zero-config default.

## Performance work deliberately deferred

- **Viewport/lazy syntax rendering** — for very large files, avoid materializing highlighted/wrapped rows that are far outside the visible viewport.
- **Bounded/spooled history** — cap in-memory historical update records and optionally spill old history to a temporary file for multi-day sessions.
- **Large-file startup index** — optional byte-offset/line index or mmap-assisted startup for multi-GB files.
- **Native backends beyond Linux/Windows** — 0.10.0 adds Linux and Windows filesystem wakeups; macOS/BSD could gain kqueue/FSEvents rather than using the polling fallback.
- **Terminal scroll-region optimization** — 0.10.0 damage rendering avoids rewriting unchanged rows, but a one-line viewport scroll still changes most physical rows. Terminal insert/delete-line or scroll-region operations could reduce that traffic further if they can be proven screen-equivalent.

## Traditional `tail` compatibility that is probably lower priority

- Byte-count mode (`tail -c` semantics).
- `+N` from-start line/byte addressing.
- Explicit descriptor-following versus filename-following modes.
- NUL-delimited records (`-z`).

## Ideas borrowed from existing tools

The most relevant references remain lnav, MultiTail and tailspin. Interactive regex search, regex highlighting and live glob discovery have now landed in htail; bookmarks, session restore, semantic warning/error navigation and a compact minimap remain the most useful ideas to borrow without turning htail into a full log-analysis engine.
