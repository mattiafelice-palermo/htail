# Agent workflow for htail

This file contains operational detail intentionally kept out of the root
`agent.md`.

## Repository map

- `src/htail_app/` — application and TUI implementation.
- `tests/` — unit and regression suite.
- `benchmarks/reference_probe.py` — deterministic behavior/performance probe.
- `benchmarks/reference_compare.py` — compares the current tree with the
  frozen `v0.9.0` behavior reference.
- `tools/build_release.py` — builds the standalone `htail` wrapper.
- `tools/build_runtime.py` — builds/self-tests runtime assets.
- `RELEASE_NOTES.md` — release changelog used by the GitHub release workflow.
- `.github/workflows/ci.yml` — feature-branch push/manual validation gate.
- `.github/workflows/release.yml` — main/tag release workflow.

## Development sequence

1. Start from the current repository state and create a purpose-specific
   branch. Use the current release number in feature/fix branch names when the
   change is release-bound; docs-only branches may stay on the current release.
2. Inspect what is relevant to the task before editing: implementation, nearby
   tests, documentation, invariants, workflows, and release machinery as
   applicable. Inspect enough to understand the blast radius and existing
   conventions; do not optimize for the fewest files read, and do not spend
   time exploring unrelated areas.
3. Edit locally with targeted patches. Review both `git diff` and
   `git diff --check` while working.
4. Add focused regression tests for behavior changes. Prefer tests that exercise
   the existing public/internal seam instead of duplicating implementation
   logic in the test.
5. Run the applicable local gate below before the first GitHub push that
   triggers CI.
6. Push/publish only the final tested files as one coherent feature commit when
   practical. The feature-branch push triggers CI exactly once for that commit.
7. Wait for the complete branch CI gate. If it fails, fetch the failing job/log,
   reproduce or cover the issue locally, fix locally, rerun the local gate, and
   only then push a replacement feature commit.
8. After CI is green, re-read the current `main` SHA. If it is still the base
   used for the tested commit, promote that exact commit to `main` (normally a
   fast-forward ref update). If `main` moved, rebuild/rebase locally on the new
   base and rerun the required local gate and CI before promotion.
9. For a versioned product change, verify the post-promotion release workflow
   and resulting release/tag.

## Local validation gates

Choose the gate from the actual changed paths and behavior, not from perceived
change size.

### Full gate

Use the full gate for anything capable of changing application, build, test,
benchmark, CI, or release behavior. This includes changes under `src/`,
`tools/`, `tests/`, `benchmarks/`, `.github/workflows/`, and the `htail`
launcher.

Install the CI test dependency if the environment does not already have it:

```bash
python -m pip install --disable-pip-version-check RapidFuzz==3.14.5
```

Before any push that triggers CI for such a change, run at least:

```bash
python -m compileall -q src tools tests benchmarks
PYTHONPATH=src python -m unittest discover -s tests -v
git diff --check
```

The full unit suite is mandatory for this gate even when the code change looks
small.

For product/release changes, also build and smoke-test the standalone wrapper:

```bash
python tools/build_release.py --output /tmp/htail
python /tmp/htail --version
```

For TUI/rendering/invariant-sensitive changes, also run the frozen behavior
comparison when the local checkout contains the `v0.9.0` tag:

```bash
PYTHONPATH=src python benchmarks/reference_compare.py --reference v0.9.0
```

If the local checkout was materialized from an archive and lacks Git tags, do
not fabricate the reference result. Record that the invariant comparison is
left to CI, which checks out full history/tags.

### Documentation-only fast path

A change is documentation-only only when it cannot alter runtime, build, test,
CI, or release execution. Typical examples are `README.md`, `agent.md`,
`AGENTS.md`, and Markdown files under `docs/`. A workflow/configuration/script
change is **not** documentation-only just because its diff is mostly comments
or prose.

For a genuine documentation-only change, the local fast path is sufficient:

```bash
git diff --check
```

Also validate the documentation affected by the change: follow relative links,
check referenced paths/commands against the repository, and verify any required
heading/template conventions. Run a targeted script or test when the edited
documentation is machine-consumed or when a repository check exists for it.

Do not run the full application unit suite merely to prove that prose did not
change Python behavior. The feature-branch push still goes through the
repository's normal full CI gate before promotion to `main`.

## GitHub transport when shell network is unavailable

Implementation should still happen entirely in the local working tree. Once
the local gate is green, the GitHub connector may be used as the transport:

Use these connector primitives directly; they are the standard transport path
for this repository and should not be rediscovered on every change:

- `create_blob(repository_full_name, content, encoding)` — upload the final
  content of one changed file and return its Git blob SHA. Independent changed
  files may be uploaded concurrently when the harness supports parallel calls.
- `create_tree(repository_full_name, tree_elements, base_tree_sha)` — create
  one candidate tree using the current `main` tree as the base and replacing
  only the changed paths with their final blob SHAs.
- `create_commit(repository_full_name, message, tree_sha, parent_sha)` — create
  exactly one feature commit whose parent is the `main` SHA used for local
  validation.
- `create_branch(repository_full_name, branch_name, sha)` — publish a new
  feature/hotfix branch at that commit. This branch push triggers CI.
- `update_ref(repository_full_name, branch_name, sha, force=false)` — move an
  existing feature branch, or after CI succeeds fast-forward `main` to the exact
  green commit.
- Workflow-run lookup (`fetch` on Actions run endpoints, or the loaded
  workflow-run/job helpers) — confirm the feature-branch CI and, after
  promotion, the release workflow. Do not repeatedly discover equivalent
  helpers if one is already loaded.

The mechanical publish sequence is:

1. After the local gate, read the current GitHub `main` SHA and its tree SHA
   once. It must still be the base used for the candidate.
2. Upload the final versions of **only the changed files** with `create_blob`.
   Do not fetch those files back from GitHub merely to reconstruct content that
   is already authoritative in the local candidate.
3. Call `create_tree` once, using the current `main` tree as `base_tree_sha`.
4. Call `create_commit` once with the current `main` SHA as parent.
5. Call `create_branch` for a new branch, or `update_ref` for an already-created
   feature branch. Do not create the branch before the final candidate commit
   exists. This update is the sole CI trigger.
6. Check CI after a sensible interval based on its normal runtime; do not
   tight-poll.
7. If the complete branch gate is green, read `main` **once more immediately
   before promotion**. If it still equals the original base, call `update_ref`
   to fast-forward `main` to the exact green feature commit.
8. For product releases, verify the release workflow and published version.

Sending a final complete file blob is acceptable transport; repeatedly replacing
whole files through the API while developing is not. Avoid temporary workflow
files, encoded patch chunks, reconstruction jobs, or extra staging commits unless
there is a demonstrated connector limitation that makes them unavoidable.

For efficient connector use:

- Do not call connector/tool discovery for the primitives listed above once
  they are available in the session. Invoke them directly.
- Reuse repository/branch/commit/tree identifiers already established in the
  session instead of repeatedly looking them up.
- Upload independent file blobs concurrently when the harness safely supports
  it; otherwise upload them serially without intermediate verification calls.
- Prefer one final set of changed-file blobs, one tree, and one commit over a
  sequence of per-file development commits. A returned blob SHA is sufficient;
  do not fetch the blob back solely to verify transport unless corruption is
  actually suspected.
- Do not refetch remote copies of files already present in the final local
  candidate. The local tested tree is the source of truth for publication.
- Check `main` at the start of publication and once immediately before
  promotion, not between every connector operation.
- Poll CI at a cadence appropriate to its normal runtime rather than in tight
  loops.
- After promotion, wait for and verify the release workflow when the
  change affects product source, versioning, build/release behavior, or when
  explicitly requested. For a documentation-only change, confirming promotion
  to `main` is normally sufficient; an unrelated release workflow need not
  block task completion.

## CI and promotion policy

The branch gate in `.github/workflows/ci.yml` includes compilation, the full
unit suite, the `v0.9.0` invariant comparison, standalone/runtime builds, and
smoke tests. It runs on pushes to branches other than `main`;
`workflow_dispatch` exists for explicit manual reruns. A green unit-test step
alone is not sufficient; wait for the whole job.

After the exact feature commit is green, promote it to `main` only if `main`
still points at the base commit used to build it. A direct fast-forward is
preferred because it preserves the exact CI-tested commit. Preserve the feature
branch unless branch deletion is explicitly requested/performed.

## Versioning and release notes

Changes under `src/` or `tools/` are product-source changes. Before merging
them, bump `VERSION` in `src/htail_app/__init__.py` and add the matching release
entry at the top of `RELEASE_NOTES.md`. The main release workflow rejects
product-source changes when the existing version already has a published tag.

Use this exact release-note shape for all new versions:

```markdown
# htail X.Y.Z

## New features

- Describe user-visible additions here.

## Bug fixes

- Describe fixes/regressions here.
```

Both `## New features` and `## Bug fixes` must be present. If one category has
no entries, write `- None.` under it. Do not invent additional second-level
categories for new releases; fold implementation/test details into the most
appropriate of these two sections. Existing historical entries may retain their
old headings.

Documentation-only changes normally do not bump `VERSION` and do not add a new
release entry.

## Reporting

Be precise about what was actually validated. Report local test counts, CI run
status, promoted commit SHA, and release status only when observed. Do not imply that a
full suite, branch deletion, release publication, or other action happened when
it did not.
