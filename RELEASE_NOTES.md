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
