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
- `.github/workflows/ci.yml` — PR/push release gate.
- `.github/workflows/release.yml` — main/tag release workflow.

## Development sequence

1. Start from the current repository state and create a purpose-specific
   branch. Use the current release number in feature/fix branch names when the
   change is release-bound; docs-only branches may stay on the current release.
2. Read the relevant implementation and nearby tests before editing. Search for
   existing helpers, state, rendering conventions, and input handling before
   introducing a new path.
3. Edit locally with targeted patches. Review both `git diff` and
   `git diff --check` while working.
4. Add focused regression tests for behavior changes. Prefer tests that exercise
   the existing public/internal seam instead of duplicating implementation
   logic in the test.
5. Run the local gate below before the first GitHub push that triggers CI.
6. Push/publish only the final tested files. Keep the PR history clean; one
   coherent feature commit is preferred when practical.
7. Wait for the complete PR CI gate. If it fails, fetch the failing job/log,
   reproduce or cover the issue locally, fix locally, rerun the local gate, and
   only then update the PR.
8. Merge normally after CI is green. For a versioned product change, verify the
   post-merge release workflow and resulting release/tag.

## Local validation gate

Install the CI test dependency if the environment does not already have it:

```bash
python -m pip install --disable-pip-version-check RapidFuzz==3.14.5
```

Before any push that triggers CI, run at least:

```bash
python -m compileall -q src tools tests benchmarks
PYTHONPATH=src python -m unittest discover -s tests -v
git diff --check
```

The full unit suite is mandatory even when the code change looks small. For a
documentation-only change, still run the full suite; it is fast and keeps the
workflow predictable.

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

## GitHub transport when shell network is unavailable

Implementation should still happen entirely in the local working tree. Once
the local gate is green, the GitHub connector may be used as the transport:

1. Confirm the current GitHub `main` SHA has not unexpectedly moved.
2. Create the feature branch from that base.
3. Upload the final versions of **only the changed files**, preferably as blobs
   plus one tree and one commit.
4. Open the PR and let the normal CI workflow run.

Sending a final complete file blob is acceptable transport; repeatedly replacing
whole files through the API while developing is not. Avoid temporary workflow
files, encoded patch chunks, reconstruction jobs, or extra staging commits unless
there is a demonstrated connector limitation that makes them unavoidable.

## CI and merge policy

The PR gate in `.github/workflows/ci.yml` includes compilation, the full unit
suite, the `v0.9.0` invariant comparison, standalone/runtime builds, and smoke
tests. A green unit-test step alone is not sufficient; wait for the whole job.

Use a normal merge commit (`merge`, not squash/rebase) unless the user requests
a different method. Preserve the feature branch unless branch deletion is
explicitly requested/performed.

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
status, merge SHA, and release status only when observed. Do not imply that a
full suite, branch deletion, release publication, or other action happened when
it did not.
