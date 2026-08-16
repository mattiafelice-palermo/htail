# htail — possible next work

This file records useful ideas that are **not part of the current release**. It is intentionally a backlog, not a commitment.

## Navigation and inspection

- **Bookmarks / marks** — mark interesting rows, jump between marks, and optionally copy/export marked rows. Inspired by lnav's bookmark workflow.
- **Semantic log-level navigation** — optional detection of ERROR/WARN/etc. with next/previous-error shortcuts. Keep this generic rather than building a full log-schema engine.
- **Session restore** — remember layout, pane focus, scroll positions, filters, search and highlights for the same source set.
- **Search-result overview** — matches could also appear on a compact scrollbar/minimap or a dedicated filtered-results drawer.
- **Global-search refinements** — the live cross-file palette now supports Simple/Regex/Boolean modes and jump-to-result. Future refinements could add mouse result selection, optional surrounding context, match grouping by file, or a compact result minimap.
- **Multiple named highlight rules** — 0.10.0 provides one persistent regex highlight per pane; a future version could support several independently styled rules.
- **Full cursor/selection mode** — 0.15.0 can reuse the currently selected search match/current token with `*`; a future visual/cursor mode could support arbitrary in-app text ranges, copying, and context actions without depending on terminal-native selection.
- **Back/forward navigation stack** — return to earlier locations after search, outline, update or global-search jumps.
- **Historical snapshot comparison / changed-only view** — make htail's captured updates more explicitly navigable and comparable as document versions.

## Sources

- **Structured JSON view** — pretty-print JSON/JSONL with optional collapse/expand while preserving the raw source for change tracking.
- **Open/add source from inside the TUI** — extend the 0.15 command palette with an `:open`-style source picker rather than restarting htail.
- **Richer dynamic-source policy** — optional automatic pane removal/archival for glob-matched files that permanently disappear.
- **SSH reconnect policy** — 0.15.0 uses the system OpenSSH client for first-class remote-tail sources; a future option could automatically reconnect a pane after transport failure while preserving its history.
- **More compressed formats / streaming decompression** — 0.15.0 supports static gzip/bzip2/xz/lzma snapshots; zstd and very-large compressed-file streaming could be added separately.

## UI ideas

- **Scrollbar/minimap** — proportional position plus marks for updates, search hits, warnings and bookmarks.
- **Custom key maps / themes** — configurable bindings and a small theme layer while retaining a sane zero-config default.
- **Command-palette expansion** — add source opening, saved actions/profiles, and configuration that no longer deserves a dedicated key.

## Performance work deliberately deferred

- **Viewport/lazy syntax rendering** — for very large files, avoid materializing highlighted/wrapped rows that are far outside the visible viewport.
- **Bounded/spooled history** — cap in-memory historical update records and optionally spill old history to a temporary file for multi-day sessions.
- **Large-file startup index** — optional byte-offset/line index or mmap-assisted startup for multi-GB files.
- **Simplify the ANSI width/clip pipeline** — several rendering stages still defensively strip, clip, pad, and measure ANSI rows more than once. Once the new render caches are established, tighten those stage invariants and remove redundant scans without changing terminal output.
- **Indexed update-title lookup** — `current_update_number()` still walks update records linearly while composing pane titles. Replace that with an indexed/bisect lookup so very long-running sessions do not accumulate title-render overhead.
- **Rectangular pane-terminal updates** — when only one pane changes, write that pane's terminal rectangle directly instead of rebuilding changed physical terminal rows spanning neighboring columns. This is the next step beyond pane-box caching if profiling still shows terminal assembly/output overhead.
- **Native backends beyond Linux/Windows** — 0.10.0 adds Linux and Windows filesystem wakeups; macOS/BSD could gain kqueue/FSEvents rather than using the polling fallback.
- **Terminal scroll-region optimization** — damage rendering and pane-box caching avoid recomputing unchanged panes, but a one-line viewport scroll can still change most physical rows inside the active pane. Terminal insert/delete-line or scroll-region operations could reduce that traffic further if they can be proven screen-equivalent.

## Traditional `tail` compatibility that is probably lower priority

- Byte-count mode (`tail -c` semantics).
- `+N` from-start line/byte addressing.
- Explicit descriptor-following versus filename-following modes.
- NUL-delimited records (`-z`).

## Ideas borrowed from existing tools

The most relevant references remain lnav, MultiTail, tailspin, klogg and ov. Simple/regex/Boolean search, global live search, regex highlighting, live glob discovery, Markdown outline navigation, line numbers/nowrap, compressed static sources, SSH sources, rate/heartbeat status and clickable URLs have now landed in htail. Bookmarks, session restore, semantic warning/error navigation, changed-only/history views and a compact minimap remain particularly useful ideas without turning htail into a full log-analysis engine.
