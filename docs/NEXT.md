# htail — possible next work

This file records useful ideas that are **not part of the current release**. It is intentionally a backlog, not a commitment.

## Navigation and inspection

- **Bookmarks / marks** — mark interesting rows, jump between marks, and optionally copy/export marked rows. Inspired by lnav's bookmark workflow.
- **Semantic log-level navigation** — optional detection of ERROR/WARN/etc. with next/previous-error shortcuts. Keep this generic rather than building a full log-schema engine.
- **Session restore** — remember layout, pane focus, scroll positions, filters, search and highlights for the same source set.
- **Search-result overview** — 0.17.0 adds a proportional scrollbar to each scrollable pane; future work could add search-hit/update/warning/bookmark marks or a dedicated filtered-results drawer.
- **Global-search refinements** — the live cross-file palette now supports Simple/Regex/Boolean/Fuzzy modes, mouse result selection, surrounding context, file grouping and jump-to-result. A compact result minimap remains useful.
- **Multiple named highlight rules** — 0.10.0 provides one persistent regex highlight per pane; a future version could support several independently styled rules.
- **Full cursor/selection mode** — 0.17.0 adds pane-local double-click token selection/highlighting with clipboard copy; a future visual/cursor mode could support arbitrary drag/keyboard ranges and context actions.
- **Back/forward navigation stack** — return to earlier locations after search, outline, update or global-search jumps.
- **Historical snapshot comparison / changed-only view** — make htail's captured updates more explicitly navigable and comparable as document versions.

## Sources

- **Structured JSON view** — pretty-print JSON/JSONL with optional collapse/expand while preserving the raw source for change tracking.
- **Open/add source from inside the TUI** — extend the command palette with an `:open`-style source picker rather than restarting htail. 0.17.0 adds pane closing, so opening/reopening sources is now the missing half of pane lifecycle management.
- **Richer dynamic-source policy** — optional automatic pane removal/archival for glob-matched files that permanently disappear.
- **SSH reconnect policy** — 0.15.0 uses the system OpenSSH client for first-class remote-tail sources; a future option could automatically reconnect a pane after transport failure while preserving its history.
- **More compressed formats / streaming decompression** — 0.15.0 supports static gzip/bzip2/xz/lzma snapshots; zstd and very-large compressed-file streaming could be added separately.

## UI ideas

- **Scrollbar/minimap marks** — 0.17.0 provides a conventional proportional per-pane scrollbar. Future work could overlay updates, search hits, warnings and bookmarks without turning it into a full minimap.
- **Custom key maps** — 0.17.0 adds persistent full-UI palettes with an in-app create/edit/save/delete palette editor; configurable key bindings remain open.
- **Command-palette expansion** — add source opening, saved actions/profiles, and configuration that no longer deserves a dedicated key.

## Performance work deliberately deferred

- **Viewport/lazy syntax rendering** — for very large files, avoid materializing highlighted/wrapped rows that are far outside the visible viewport.
- **Bounded/spooled history** — cap in-memory historical update records and optionally spill old history to a temporary file for multi-day sessions.
- **Large-file startup index** — optional byte-offset/line index or mmap-assisted startup for multi-GB files.
- **Simplify the ANSI width/clip pipeline** — several rendering stages still defensively strip, clip, pad, and measure ANSI rows more than once. Once the new render caches are established, tighten those stage invariants and remove redundant scans without changing terminal output.
- **Indexed update-title lookup** — `current_update_number()` still walks update records linearly while composing pane titles. Replace that with an indexed/bisect lookup so very long-running sessions do not accumulate title-render overhead.
- **Horizontal-margin scroll regions** — 0.16.16 uses native terminal scroll regions only for full-width panes and rectangular writes for Columns/Grid. A future optimization could evaluate DECSLRM/left-right margin capability detection to combine native scrolling with side-by-side panes without assuming terminal support.
- **Native backends beyond Linux/Windows** — 0.10.0 adds Linux and Windows filesystem wakeups; macOS/BSD could gain kqueue/FSEvents rather than using the polling fallback.

## Traditional `tail` compatibility that is probably lower priority

- Byte-count mode (`tail -c` semantics).
- `+N` from-start line/byte addressing.
- Explicit descriptor-following versus filename-following modes.
- NUL-delimited records (`-z`).

## Ideas borrowed from existing tools

The most relevant references remain lnav, MultiTail, tailspin, klogg and ov. Simple/regex/Boolean/Fuzzy search, global live search, regex highlighting, live glob discovery, Markdown outline navigation, line numbers/nowrap, compressed static sources, SSH sources, rate/heartbeat status, clickable URLs, pane closing, spatial pane navigation, proportional pane scrollbars, UI palettes and double-click token copy have now landed in htail. Bookmarks, session restore, semantic warning/error navigation, changed-only/history views and compact scrollbar marks remain particularly useful ideas without turning htail into a full log-analysis engine.
