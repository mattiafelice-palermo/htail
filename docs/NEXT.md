# htail — possible next work

This file records useful ideas that are **not part of the current release**. It is intentionally a backlog, not a commitment.

## Navigation and inspection

- **Interactive search** — `/regex`, then `n` / `N` for next/previous match. Search hits could also appear on a compact scrollbar/minimap.
- **Bookmarks / marks** — mark interesting rows, jump between marks, and optionally copy/export marked rows. Inspired by lnav's bookmark workflow.
- **User highlight rules** — interactive or CLI-defined regex highlights, independent of syntax highlighting.
- **Semantic log-level navigation** — optional detection of ERROR/WARN/etc. with next/previous-error shortcuts. Keep this generic rather than building a full log-schema engine.
- **Session restore** — remember layout, pane focus, scroll positions, filters, search and highlights for the same source set.

## Sources

- **Dynamic glob/directory watching** — keep a pattern such as `logs/*.log` live and add panes when new matching files appear.
- **Structured JSON view** — pretty-print JSON/JSONL with optional collapse/expand while preserving the raw source for change tracking.
- **Open/add source from inside the TUI** — an `:open`-style command palette rather than restarting htail.

## UI ideas

- **Scrollbar/minimap** — proportional position plus marks for updates, search hits, warnings and bookmarks.
- **Cursor/selection mode** — select/copy lines without fighting terminal mouse selection; potentially expose context actions for search/filter/highlight.
- **Custom key maps / themes** — configurable bindings and a small theme layer while retaining a sane zero-config default.

## Performance work deliberately deferred

- **Native filesystem notifications** — inotify on Linux, ReadDirectoryChangesW on Windows, kqueue/FSEvents where appropriate, with polling as the portable fallback. This should reduce idle stat/read activity and latency.
- **Terminal damage rendering** — retain the previous screen buffer and rewrite only physical rows that changed instead of repainting the complete screen on every scroll frame.
- **Viewport/lazy syntax rendering** — for very large files, avoid materializing highlighted/wrapped rows that are far outside the visible viewport.
- **Bounded/spooled history** — cap in-memory historical update records and optionally spill old history to a temporary file for multi-day sessions.
- **Large-file startup index** — optional byte-offset/line index or mmap-assisted startup for multi-GB files.

## Traditional `tail` compatibility that is probably lower priority

- Byte-count mode (`tail -c` semantics).
- `+N` from-start line/byte addressing.
- Explicit descriptor-following versus filename-following modes.
- NUL-delimited records (`-z`).

## Ideas borrowed from existing tools

The most relevant references are lnav, MultiTail and tailspin. The useful lesson is not to reproduce their entire log-analysis stacks: htail's distinctive niche is stateful following of arbitrary human-readable files, including whole-file rewrites and Markdown coordination documents. Search, bookmarks, highlights, sessions and dynamic source discovery fit that niche particularly well.
