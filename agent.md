# htail agent instructions

This is the short repository-level contract for coding agents. Read
[`docs/AGENT_WORKFLOW.md`](docs/AGENT_WORKFLOW.md) for commands, repository map,
release procedure, and failure handling.

## Hard rules

- Work from a real local checkout and edit files directly. Prefer targeted
  edits (`apply_patch`, small replacements, normal editor operations) and
  inspect `git diff`. Do not develop by repeatedly reconstructing whole source
  files through a remote API.
- Inspect the task-relevant implementation, tests, documentation, invariants,
  and workflows far enough to understand the change and its blast radius.
  Reuse the current architecture where possible; avoid unrelated exploration.
- Use the validation gate appropriate to the files changed. Runtime/build/test/
  release-behavior changes require the full local unit suite before any push
  that can trigger GitHub CI. Genuine documentation-only changes may use the
  docs-only fast path in `docs/AGENT_WORKFLOW.md`. Never push a known failure,
  and report exactly which gate was run.
- After local validation, publish the exact final tree efficiently: normally
  one feature commit and one PR. If the local shell cannot push to GitHub, it
  is fine to upload the final changed-file blobs/tree once through the GitHub
  connector. Do not create temporary Actions workflows, patch-transport
  commits, or staging files just to move tested code to GitHub.
- If CI fails, diagnose the failure, fix it locally, rerun the required local
  gate, then update the PR. Do not iterate by using CI as the test runner.
- Merge only after the complete PR CI gate is green. Use the normal **merge**
  method unless explicitly instructed otherwise. Do not claim a branch was
  deleted unless it actually was.
- Product-source changes (`src/` or `tools/`) require a version bump and release
  notes. Documentation-only changes do not bump the version unless requested.
- Every new release entry in `RELEASE_NOTES.md` must use exactly these two
  second-level sections: **New features** and **Bug fixes**. Keep both sections
  present; use `- None.` when a section has no entries. Do not retroactively
  rewrite old release entries unless requested.

## Efficiency

Do not spend time retrying direct GitHub network access from the VM if it is
unavailable. Use the connected GitHub tooling for repository reads/writes and
keep implementation/testing local. Keep tool use proportionate to the task:
batch independent operations when practical, avoid rediscovering connector
capabilities already known in the session, and do not block on unrelated
post-merge workflows.
