# htail 0.16.14

## New features

- Local Git files now begin remote-source discovery in the background as soon as they are opened, so the source picker can use already-prepared branch metadata instead of starting network work on demand.
- The source picker now filters filter-capable remotes to branches where the currently viewed repository-relative file actually exists, ranks the remote branch matching the local branch first, and marks it with `★` as the recommended choice.
- htail now warms the recommended remote branch's file blob in the background; after discovery finishes, switching to that branch normally requires no network work at selection time.

## Bug fixes

- Update release notes are now scoped to the current htail release and empty-section placeholders are suppressed in the update modal.
- GitHub releases now publish only the current version's release-note block, with empty-section placeholders removed, instead of attaching the complete historical `RELEASE_NOTES.md` file.

# htail 0.16.13

## New features

- None.

## Bug fixes

- Remote Git file sources now use an htail-owned blobless partial-clone cache on filter-capable remotes, fetching commit/tree metadata first and demand-fetching only the selected file blob. The previous shallow snapshot fetch remains as a compatibility fallback.
- Interactive self-update now makes the 100% `Update complete` state render before restart scheduling continues, so a successful update cannot jump directly from an intermediate percentage to the restarted application.
- Self-update now labels real release, runtime, and checksum connection attempts and retries as explicit progress stages, so a slow TLS/network connection is visible instead of looking frozen at a fixed percentage.

# htail 0.16.12

## New features

- None.

## Bug fixes

- Self-update now uses SHA-256 digests already supplied by the GitHub release API for executable and runtime assets, avoiding redundant checksum-file HTTPS requests on normal GitHub releases.
- Small GitHub update/checksum requests now use bounded retries with short backoff for transient TLS, timeout, and transport failures instead of failing immediately on a single handshake timeout.
- Agent workflow guidance now documents the exact GitHub connector primitives and mechanical blob/tree/commit/branch publication sequence so future agents can publish without repeated tool discovery or redundant file fetches.

# htail 0.16.11

## New features

- None.

## Bug fixes

- Fixed a 0.16.10 startup crash when rendered Markdown contains fenced code blocks. The app-layer Markdown renderer now uses the existing frozen-core Pygments helper correctly.
- Added regression coverage for fenced Markdown code rendering through the app-layer highlighter extension.

# htail 0.16.10

## New features

- Remote-backed panes now use a distinct border accent and a prominent `REMOTE <remote/branch>` title badge so it is immediately clear when a pane is no longer showing the local working tree.
- Rendered Markdown now supports pipe tables with aligned columns and rendered separator rules. Table rows are never soft-wrapped; wide tables can be horizontally scrolled while ordinary Markdown remains wrapped.
- Rows and Columns layouts can now be resized interactively: `Ctrl+Up` / `Ctrl+Down` shrink/grow the focused row, `Ctrl+Left` / `Ctrl+Right` shrink/grow the focused column, and the command palette can equalize pane sizes again.

## Bug fixes

- Initial Git source switches reuse the branch SHA already discovered by the source picker instead of performing a second `ls-remote`, reuse locally available commit objects when possible, and use a shallow private htail ref for the fallback fetch instead of fetching normal branch history into `FETCH_HEAD`.

# htail 0.16.9

## New features

- The Git file-source picker now uses a dedicated, wider modal with the watched repository-relative path shown once at the top, a clear current-source summary, grouped remote branches, and inline filtering.
- Opening the Git source picker and switching to a remote branch now run asynchronously, so the TUI stays responsive while branch metadata or remote file contents are being loaded.
- The source picker now shows live activity feedback while Git work is in progress, including phase text and an indeterminate progress bar instead of a silent pause.

## Bug fixes

- Switching to a remote branch no longer freezes the interface during the initial `ls-remote` / `fetch` / `show` cycle.
- Validation CI now runs once on the feature-branch push, with a manual `workflow_dispatch` entry point retained for explicit reruns; the exact green commit can then be promoted to `main` without a duplicate PR CI cycle.

# htail 0.16.8

## Git remote file sources

- Files opened from a Git working tree can now switch source from the local working copy to the same repository-relative file on a remote branch.
- The command palette exposes `Switch file source…`, discovers configured remotes and branches, and keeps the file identity fixed so users never need to re-enter its path.
- Remote-backed panes poll the selected remote branch, fetch only when its head changes, and feed the resulting Git blob through htail's existing snapshot/diff pipeline.
- Remote-backed pane titles show the selected `remote/branch`; the source picker marks **Local working tree** explicitly and can switch the same pane back without changing normal local-pane titles.
- Remote authentication and transport remain owned by the user's normal Git configuration, SSH agent, and credential helpers.

## Resilience and coverage

- Remote branch queries fall back to cached remote-tracking refs when a live query fails.
- Missing remote files and transient Git errors keep the last good snapshot visible instead of terminating htail.
- Added local bare-repository regression coverage for repository discovery, branch selection, source switching, and remote update tracking.

# htail 0.16.7

## Global search preview navigation

- The preview now wraps long source lines by default so the full selected line and its surrounding context can be read without leaving global search.
- Preview context has an independent viewport: Ctrl+Up / Ctrl+Down moves one source line and Ctrl+PgUp / Ctrl+PgDn moves by larger steps without changing the selected search result.
- Mouse-wheel input follows the pointer: over the preview it scrolls context, while over the results list it continues to navigate matches.
- Ctrl+W toggles wrapping. With wrapping disabled, Left / Right horizontally scrolls the preview while preserving the match-aware initial position.
- Selecting a different search result recenters the preview on that result and clears manual vertical/horizontal offsets.

## Input and regression coverage

- Added terminal decoding for Ctrl+arrow and Ctrl+Page navigation, including Windows modifier handling.
- Added renderer and interaction coverage for wrapping, independent context scrolling, nowrap horizontal scrolling, viewport reset, and pointer-sensitive mouse-wheel behavior.
- Preserved the established `Ctrl+O(letter) sort` hint when the preview is hidden.

# htail 0.16.6

## Global search preview

- The selected preview line now automatically shifts horizontally to keep the matched text visible when the line is wider than the preview pane.
- The preview keeps surrounding context lines at their normal left edge while using ellipses on the selected line to indicate clipped text.
- Match highlighting now accounts for tab expansion before calculating the preview span.

## Regression coverage

- Added narrow-preview coverage for far-right matches, full global-search rendering, and tab-expanded highlight alignment.

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
