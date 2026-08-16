# htail 0.16.1

## Bug fixes

- Fixed standalone `Esc` on POSIX/WSL terminals. Escape-sequence parsing no longer blocks waiting for another key, so Esc reliably closes the command palette, global search, local search/highlight, layout chooser, help and update confirmation.
- Global-search backend and rendering failures are now contained inside the search workspace instead of terminating htail.
- Added direct regression coverage for color-enabled global-search typing and for Esc across every dismissible modal.

## Distribution and CI

- Split native dependencies out of the universal `htail` core. Releases now publish a small core plus `htail-runtime-cp310.zip` through `htail-runtime-cp314.zip`; the updater downloads only the runtime matching the Python currently running htail.
- Runtime download, SHA-256 verification and unpacking now happen inside the update workflow before restart, with visible progress. The small application payload is also pre-extracted before restart.
- Runtime caches are keyed by the dependency-manifest hash, so later htail updates reuse the prepared native runtime when dependencies are unchanged.
- A fresh manual installation can bootstrap only its matching runtime if needed. The 0.16.1 wrapper can also directly reuse an already-extracted 0.16.0 RapidFuzz runtime during this transition.
- Normal CI builds/tests only the current CPython 3.13 runtime asset; release builds still create runtime assets for CPython 3.10–3.14.
- Native wheels are stored in runtime assets without recompression, eliminating the expensive wheel unzip + DEFLATE-9 recompression step introduced in 0.16.0.
