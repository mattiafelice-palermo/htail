# htail 0.8.1

## New features

- Modal-style overlays now preserve the live pane view in the background and dim it, so update, layout, and help dialogs read as true terminal modals rather than replacing the screen.
- The in-app updater now shows live installation phases and a download progress bar with percentage and byte counts when the server provides a content length; otherwise it shows an indeterminate progress animation.

## Bug fixes

- Fixed modal panel top-border/title composition so centered titles no longer break or misalign the upper border.
- Update installation now stays responsive while the download and checksum verification run in a background worker, while file watching continues underneath the modal.
