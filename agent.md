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
  one feature commit and one feature-branch push. That push triggers CI. After
  the exact commit is green, confirm `main` has not moved and promote that
  tested commit to `main`; do not open a PR unless explicitly requested. Use
  `workflow_dispatch` only when a manual CI run is actually needed. If the
  local shell cannot push to GitHub, it is fine to upload the final changed-file
  blobs/tree once through the GitHub connector. Do not create temporary Actions
  workflows, patch-transport commits, or staging files just to move tested code
  to GitHub.
- When connector transport is required, use the known GitHub primitives
  directly rather than rediscovering them: `create_blob` for each final changed
  file, `create_tree` once over the current `main` tree, `create_commit` once,
  then `create_branch` (new feature branch) or `update_ref` (existing branch).
  After CI is green, use `update_ref` to fast-forward `main` to that exact
  commit. Use workflow-run lookup only to verify the branch CI and release run.
- If CI fails, diagnose the failure, fix it locally, rerun the required local
  gate, then push a replacement feature commit. Do not iterate by using CI as
  the test runner.
- Promote only the exact feature commit that passed the complete CI gate. If
  `main` moved after the tested commit was based, rebase/rebuild locally and
  revalidate instead of silently promoting a stale commit. Do not claim a
  branch was deleted unless it actually was.
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
post-promotion workflows.
