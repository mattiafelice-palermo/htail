# htail 0.8.3

## Bug fixes

- Fixed the interactive self-update crash introduced in 0.8.1: confirming an update now runs the installer worker correctly instead of raising `AttributeError: 'MultiApp' object has no attribute '_install_worker'`.
- Added a regression test that exercises the actual `Y` confirmation path, runs the worker synchronously under test, and verifies that a successful install schedules the automatic restart.
