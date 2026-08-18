# Spec 001 — Safe non-text handling and terminal-cell-correct rendering

Status: **Planned**  
Type: **Input safety / terminal rendering / Unicode geometry**  
Branch: `feature/0.17.3-safe-non-text-terminal-rendering`  
Review document: [`reviews/001-safe-non-text-terminal-rendering-review.md`](reviews/001-safe-non-text-terminal-rendering-review.md)

## Summary

htail is designed for human-readable text, but currently attempts to decode and render arbitrary file contents as text.

This exposes two related problems:

1. binary or otherwise non-readable files are opened without warning because decoding uses replacement characters rather than failing;
2. arbitrary decoded Unicode and control characters can violate assumptions made by the terminal renderer, causing pane borders and other UI elements to appear in the wrong physical terminal columns or allowing source content to influence terminal state.

This spec makes arbitrary local file input safe to inspect by adding:

- content-based detection of files that are unlikely to be readable text;
- an interactive warning before suspicious local files are displayed;
- terminal-safe rendering of untrusted source characters;
- terminal-cell-aware width, clipping, wrapping, slicing, and padding instead of Python-character-count geometry.

The solution must not classify files by extension.

This spec has no child specs. The workflow therefore treats parent Spec `001` itself as the active implementation unit.

## Current behavior

Local files are currently decoded using the configured encoding with replacement semantics. Invalid byte sequences therefore become replacement characters rather than producing a decoding failure.

As a result, a PDF or other binary file can be converted into a large Python string and passed through the normal text-rendering pipeline.

The current terminal geometry also still assumes in important paths that:

```text
len(rendered_text) == number of terminal cells occupied
```

This is not generally true.

Examples include:

- CJK characters, which commonly occupy two terminal cells;
- combining characters, which may occupy zero additional cells;
- emoji and other wide Unicode sequences;
- tabs, whose width depends on the current column;
- ANSI sequences, which occupy zero cells;
- source control characters capable of affecting terminal state.

The existing `terminal_cells.py` support normalizes literal tabs, but does not yet provide a complete terminal-cell geometry abstraction.

## Goals

The implementation must:

1. identify local files that are probably not readable text using their content rather than their name;
2. warn the user before suspicious files are rendered interactively;
3. prevent source file contents from executing terminal control operations;
4. render valid Unicode text without breaking pane geometry;
5. preserve existing behavior for ordinary text files.

## Locked decisions

### Content detection must not use filename extensions

Whether a file looks readable must be determined from a bounded sample of its raw bytes.

Filename extension, MIME type inferred from the filename, and known format signatures must not be the primary classifier.

A file renamed from:

```text
document.pdf
```

to:

```text
document.txt
```

must receive the same classification.

Known magic bytes may be used only as supplementary evidence if useful; the implementation must remain fundamentally content-based.

### Detection must be encoding-aware

The configured `--encoding` must be taken into account.

The detector should use a combination of signals such as:

- whether the sampled bytes decode strictly with the requested encoding;
- invalid/decode-error density;
- NUL/control-character density after decoding;
- printable-character density;
- other deterministic readability metrics justified by tests.

The exact scoring model and thresholds are implementation details, but must be deterministic and tested.

Correctly encoded legitimate text must not be rejected merely because its byte representation contains zero bytes or non-ASCII bytes.

In particular, tests must cover:

- ASCII;
- ordinary UTF-8;
- non-ASCII UTF-8;
- correctly selected UTF-16;
- binary/random data;
- PDF-like binary data.

Detection must inspect a bounded sample rather than reading the complete file solely for classification.

## Suspicious-file UX

### Interactive local files

Before entering the normal content display for directly opened suspicious local files, htail must show a warning identifying the affected file.

The warning must communicate that the file does not appear to contain readable text in the selected encoding.

Continuing must require explicit confirmation.

Default behavior is **No**. Pressing Enter without affirmative confirmation must not open the suspicious source.

If several initial files are supplied, htail should handle suspicious sources without preventing clearly readable sources from opening.

If the user rejects every available source, htail should exit cleanly rather than opening an empty full-screen interface.

The exact wording and presentation may follow existing htail modal/prompt conventions.

### Non-interactive mode

Non-interactive operation must never wait for confirmation.

For suspicious content:

- emit a clear warning to stderr;
- continue with the terminal-safe best-effort decoded representation.

This preserves tail-like scripting behavior while making the condition visible.

### Dynamic and non-local sources

Startup confirmation is required only where htail can inspect the content before displaying it.

Sources that arrive after startup, such as dynamically discovered files or stream/remote content, do not need the same confirmation flow in this spec.

They remain subject to the rendering-safety requirements below.

## Source-content sanitization

Source file content must be treated as untrusted terminal data.

Decoded source characters must not be able to execute terminal control operations.

Before source text is handed to output-producing rendering paths, unsafe control characters must be converted to a visible deterministic representation.

At minimum this includes relevant:

- ESC;
- BEL;
- backspace;
- NUL;
- DEL;
- C0 controls;
- C1 controls;
- carriage returns occurring as content rather than ordinary line termination.

Normal structural newline handling remains unchanged.

Tabs may continue through the existing dedicated tab normalization path, provided their geometry remains deterministic.

Source-provided escape sequences must never be emitted as active ANSI/OSC/terminal sequences.

For example, a source containing bytes equivalent to:

```text
ESC [ 2 J
```

must display harmlessly rather than clear the terminal.

htail-generated ANSI styling, hyperlinks, cursor management, and other application-controlled terminal sequences must continue to work normally.

Where practical within the existing architecture, sanitization should occur in the display representation rather than altering the canonical decoded snapshot used for:

- change detection;
- file identity;
- search;
- filtering.

The implementation should avoid changing semantic source data merely to make output safe.

## Terminal-cell geometry

All renderer logic whose purpose is terminal geometry must operate in **terminal cells**, not Python code-point count.

This applies to the relevant paths for:

- width measurement;
- clipping;
- padding;
- wrapping;
- horizontal slicing;
- pane body rows;
- pane titles;
- borders;
- composed multi-pane rows.

ANSI sequences generated by htail must have zero width.

The implementation should establish one shared ANSI-aware terminal-cell abstraction rather than adding isolated fixes to individual borders.

A maintained terminal-width implementation such as `wcwidth` should be preferred over a custom partial Unicode-width table.

If a new runtime dependency is introduced, it must be pinned and included through the repository's existing bundled-runtime mechanism.

An efficient ASCII fast path should be retained so ordinary logs do not incur unnecessary per-character Unicode-width overhead.

## Unicode behavior

Valid text containing the following must render without shifting the pane border:

- ordinary ASCII;
- accented Latin text;
- combining marks;
- CJK wide characters;
- representative emoji;
- mixed narrow/wide text;
- existing literal-tab cases.

The renderer must not assume that one Unicode code point equals one terminal cell.

Where the chosen width library has defined limitations for complex grapheme/emoji sequences, behavior should follow that library consistently rather than introducing independent ad-hoc rules.

## Existing architecture

The implementation should extend the current modular rendering architecture.

In particular:

- prefer extending `terminal_cells.py` or adding a narrowly scoped companion module;
- avoid rewriting the frozen compatibility `core.py` unless there is a demonstrated architectural reason;
- reuse existing pane rendering, follower, and UI seams;
- do not duplicate clipping/wrapping implementations unnecessarily.

The existing tab normalization work should be integrated into the general terminal-cell model rather than bypassed.

## Regression requirements

The change must preserve existing behavior for normal text sources, including:

- syntax highlighting;
- Markdown rendering;
- Markdown tables;
- ANSI styling;
- OSC-8 links;
- search and selected-match highlighting;
- regex highlighting;
- line numbers;
- wrapping;
- horizontal scrolling;
- CHANGES and TAIL modes;
- update markers;
- pane resizing;
- multi-pane rows, columns, and grid layouts;
- damage rendering.

The existing tab-related border regression must remain covered.

## Required tests

### Text-likelihood classifier

- ordinary ASCII is accepted;
- normal UTF-8 is accepted;
- UTF-8 containing wide Unicode is accepted;
- correctly configured UTF-16 text is accepted;
- binary/PDF-like data is suspicious;
- binary data remains suspicious after changing its filename extension;
- bounded sampling is used for large files.

### Terminal safety

Source content containing representative raw terminal controls must not result in those controls appearing as executable terminal sequences in rendered pane output.

Cover at least:

- ESC;
- BEL;
- backspace;
- NUL;
- DEL;
- representative C1 control;
- embedded carriage return.

### Terminal-cell geometry

For known pane widths, rendered physical width must remain exact with:

- ASCII;
- CJK;
- combining marks;
- mixed narrow/wide Unicode;
- representative emoji;
- ANSI-styled Unicode;
- tabs.

Pane side borders must remain in the expected terminal column.

Tests must verify terminal-cell width, not merely `len()`.

### Layout integration

At minimum verify width-exact behavior for:

- one pane;
- columns;
- rows or grid where relevant to shared row composition.

Existing rendering tests must remain green.

## Documentation and release requirements

This is a product-source change.

The implementation must therefore:

- bump the htail version from `0.17.2` to `0.17.3`;
- add a `0.17.3` entry to `RELEASE_NOTES.md`;
- use exactly the repository-required second-level sections:
  - `New features`
  - `Bug fixes`
- update README documentation where the new warning behavior or terminal-safety behavior is user-visible.

## Validation

Before implementer handoff, follow repository validation rules.

Required focused checks include the new classifier, sanitization, and terminal-cell regression tests.

Then run the repository canonical full validation for product/TUI changes, including:

```bash
python -m compileall -q src tools tests benchmarks
PYTHONPATH=src python -m unittest discover -s tests -v
git diff --check
```

Also build and smoke-test the standalone wrapper:

```bash
python tools/build_release.py --output /tmp/htail
python /tmp/htail --version
```

Because this is rendering/invariant-sensitive, run:

```bash
PYTHONPATH=src python benchmarks/reference_compare.py --reference v0.9.0
```

when the local checkout contains the required reference tag.

If the tag is unavailable locally, report that fact rather than inventing the comparison result.

## Acceptance criteria

1. A binary/PDF-like local file produces a readability warning based on content, not its extension.
2. Renaming that same binary file to a text-looking extension does not bypass detection.
3. Normal text does not produce the warning.
4. Interactive suspicious-file opening requires explicit confirmation and defaults to declining.
5. Non-interactive operation never waits for confirmation.
6. Source-controlled terminal escape/control characters cannot affect terminal state.
7. Valid wide and zero-width Unicode does not break pane borders.
8. Terminal geometry uses shared ANSI-aware cell-width primitives rather than Python `len()` in the affected layout paths.
9. Existing tab-border behavior remains correct.
10. Existing ordinary-text rendering behavior remains compatible.
11. Focused regression tests cover classifier, sanitization, Unicode width, and pane geometry.
12. Repository canonical validation passes.
13. The standalone release build reports version `0.17.3`.
14. README and release notes document the user-visible change.
