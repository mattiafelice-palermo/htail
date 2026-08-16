# htail 0.15.0

## New features

- Added a searchable `:` command palette with a Markdown heading outline, pane switching and configuration actions.
- Added Boolean search (`AND`, `OR`, `NOT`, parentheses and quoted phrases) as a third search mode alongside Simple and Regex.
- Added `*` search-selected/current-word behavior.
- Added per-pane line numbers and wrap-off mode with horizontal `←` / `→` scrolling.
- Added rolling line/byte rate meters and configurable expected-heartbeat alerts (`--heartbeat 5m`).
- Added direct static viewing of `.gz`, `.bz2`, `.xz` and `.lzma` files.
- Added first-class OpenSSH remote-tail sources via `--ssh user@host:/path` or `--ssh ssh://host/path`.
- `--exec` and SSH source panes now expose process PID, runtime and exit status in their lifecycle state.
- HTTP(S) URLs are emitted as OSC-8 hyperlinks for terminals that support clickable links.

## Notes

- Compressed inputs are intentionally static snapshots; they are not re-read when the compressed file changes.
- SSH transport/authentication uses the installed `ssh` command and therefore respects the user's normal OpenSSH configuration.
