# Review — Spec 001: Safe non-text handling and terminal-cell-correct rendering

Status: **Changes required**
Branch: `feature/0.17.3-safe-non-text-terminal-rendering`
Active work unit: `001`

## Review scope

Reviewed the feature diff against Spec 001, the repository agent guidance, and the current rendering/search architecture. The primary pane path now sanitizes source controls and uses shared terminal-cell helpers, but several output paths and Unicode geometry cases remain outside the stated safety/width contract.

## Findings

### R1 — High: global-search rendering can still execute source-provided terminal escapes

Affected files:
- `src/htail_app/global_search.py`
- `tests/test_safe_non_text_0173.py` or focused global-search tests

**Current**

`build_corpus()` intentionally keeps `pane.snapshot_raw` unsanitized, which is consistent with the spec's requirement to preserve canonical source semantics for search. However, the same raw `CorpusLine.text` and raw preview lines are then rendered directly by `_flat_result_rows()`, `_grouped_result_rows()`, and `_preview_rows()`.

Those rows eventually pass through `core.clip_ansi()`. In the new terminal-cell implementation, recognized CSI/OSC sequences are deliberately treated as zero-width application ANSI and preserved. Therefore a source line containing a real escape sequence such as `ESC [ 2 J` can still reach the terminal as an active sequence when displayed in global-search results/preview, even though the ordinary pane renderer sanitizes it.

This violates the locked requirement that source content must not be able to execute terminal controls and that dynamic/non-local sources remain subject to rendering safety.

**Target**

Keep the search corpus and match indices based on the canonical unsanitized source text, but sanitize source-derived text at the global-search display boundary before any ANSI-aware clipping/padding/output operation. Preserve htail-generated highlighting and styling.

**Acceptance criteria**

- A regression test builds a pane/snapshot containing representative source `ESC`, OSC/CSI-like content, BEL/C1 controls and renders global-search result/preview rows.
- No source-provided raw terminal control sequence survives in the rendered output.
- Search matching still operates on the original canonical source text rather than the sanitized display representation.
- htail-generated search highlighting/styling remains functional.

### R2 — Medium: global-search layout still uses Python character counts instead of terminal cells

Affected files:
- `src/htail_app/global_search.py`
- `src/htail_app/terminal_cells.py` as needed
- focused global-search rendering tests

**Current**

The spec requires terminal geometry to use terminal cells throughout relevant rendering paths. Global search still measures and slices visible UI content with code-point operations, including `len(core.strip_ansi(...))`, `len(preview)`, direct `text[left:right]` / `text[view_left:view_left + room]` slicing, and final right-padding based on `len(core.strip_ansi(row))`.

`_pad()` does call the new cell-aware `core.clip_ansi()`, but then recomputes visible width with `len()`. As a result, CJK, combining marks, and emoji in source text or source names can still make global-search rows/panel borders occupy the wrong physical columns.

**Target**

Use the shared terminal-cell abstraction for global-search display width, clipping, padding, horizontal slicing, preview wrapping, and composed-row padding. Semantic search/match indices may remain code-point based, but the display projection must translate them safely into cell-aware rendered spans rather than assuming one code point per cell.

**Acceptance criteria**

- Flat result rows containing CJK, combining text, and emoji render to exactly the requested terminal-cell width.
- Grouped/file rows and preview rows with the same classes of Unicode remain width-exact.
- Horizontal preview scrolling/slicing does not shift or overrun the panel border.
- Tests assert terminal-cell width, not `len()`.

### R3 — Medium: complex emoji/ZWJ width measurement is internally inconsistent

Affected files:
- `src/htail_app/terminal_cells.py`
- `tests/test_safe_non_text_0173.py` or a focused terminal-cell test module

**Current**

`display_width()` delegates whole plain runs to `wcswidth()`, while clipping, slicing and wrapping iterate individual code points and add `wcwidth()` for each one. For Unicode sequences whose width is defined at the sequence level (notably ZWJ/variation-selector emoji), these two code paths can disagree: the same rendered string can be measured as one width by `display_width()` and consumed as a different width by `clip_cells_ansi()`, `slice_cells_ansi()`, or `_wrap_ansi_cells()`.

The current tests cover only a single-code-point emoji (`🙂`), so they do not exercise this inconsistency. This conflicts with the spec requirement to follow the chosen width library consistently for complex grapheme/emoji sequences.

**Target**

Make measurement and cell-consuming operations use a consistent sequence-aware width model. Do not introduce an independent ad-hoc emoji table that disagrees with the bundled `wcwidth` semantics.

**Acceptance criteria**

- Add focused coverage for at least one ZWJ emoji sequence and one variation-selector/emoji-presentation sequence supported by the bundled width library.
- `display_width`, clipping, wrapping, padding, and horizontal slicing agree on the occupied cells for those sequences.
- Pane borders remain width-exact when such sequences appear near a clipping/wrapping boundary.

### R4 — Medium: suspicious direct files bypass confirmation in interactive pipe-driven sessions

Affected files:
- `src/htail_app/app.py`
- focused startup/CLI tests

**Current**

`main()` can choose interactive mode when `stdout` is a TTY and stdin is a watched pipe (`-`), or when command/SSH/glob sources are present, even if `sys.stdin.isatty()` is false. But `_confirm_initial_local_sources(args)` is invoked only inside `if sys.stdin.isatty()`.

Consequently, an invocation that is still interactive, such as a piped stdin source together with a directly named suspicious local file, can enter the full-screen UI and open that suspicious file without the explicit confirmation required by the spec.

**Target**

For every interactive startup, directly named suspicious local files must not be opened without an affirmative confirmation. Use the controlling-terminal input path where available; if an interactive confirmation channel genuinely cannot be obtained, fail closed for the suspicious direct source rather than silently opening it. Keep non-interactive behavior unchanged: warn on stderr and continue without prompting.

**Acceptance criteria**

- Add a regression test for interactive mode with non-TTY stdin plus a directly named suspicious file (for example a `-` source or other existing interactive source).
- The suspicious direct file is rejected by default unless affirmative confirmation is received.
- Ordinary non-interactive execution still never waits for input and continues with a warning.

## Verification notes

The initial implementer handoff reported focused Spec 001 tests, compileall, diff check, frozen-reference comparison, and release build/version smoke as passing. The reported local full suite had host/platform failures; final review must verify the repository's canonical branch gate/acceptance evidence before completion.

## Review round 2 — returned fixes

R1 is **resolved**. Global-search result/file/preview display now projects raw source text through sanitization while preserving the canonical raw corpus and translating match boundaries; focused coverage verifies raw CSI/BEL/C1 content does not survive as executable terminal input and htail highlighting remains present.

R2 is **resolved**. Global-search padding, row composition, preview wrapping/horizontal slicing, file-name rendering, and final panel padding now use terminal-cell helpers. Focused tests cover relevance/file layouts, preview hscroll, CJK, combining text, emoji, and width-exact output.

R3 is **resolved**. Terminal-cell iteration now groups relevant combining/variation/ZWJ units and uses `wcswidth()` consistently for the same unit across measurement, clipping, slicing, wrapping and tab projection. Focused tests cover a ZWJ sequence and emoji-presentation variation sequence, including pane-border exactness.

R4 is **resolved**. Interactive startup now always filters suspicious direct files; when stdin is a source pipe it attempts a controlling-terminal input stream and fails closed when none is available. Focused coverage exercises the non-TTY-stdin interactive path.

### R5 — High: Markdown outline palette still renders raw source controls

Affected files:
- `src/htail_app/app.py`
- `src/htail_app/extras.py` if the sanitization seam belongs there
- focused command-palette/outline safety tests

**Current**

The source-safety contract is still bypassed by the Markdown outline UI. `_palette_all_items()` calls `markdown_outline(pane.snapshot_raw)` and places `entry.text` directly into `PaletteItem.label`. Palette rendering then appends `item.label` to terminal rows. Because the label is not sanitized first, source-provided `ESC`/CSI/OSC/control characters in a Markdown heading can again be interpreted as terminal control sequences when the user opens the outline palette.

This is the same trust-boundary problem fixed for the ordinary pane and global search: canonical source data may remain raw, but every output projection of source-derived text must be safe.

**Target**

Keep outline parsing/source indices based on canonical raw Markdown, but sanitize heading text before it becomes a rendered palette label. Audit the immediately related source-derived palette/display path so the fix is made at the correct display seam rather than as a one-off escape replacement.

**Acceptance criteria**

- A Markdown snapshot containing a heading with representative `ESC`/CSI, BEL and C1 controls can still produce an outline entry/source index.
- Rendering the outline palette contains visible safe representations and no executable source-provided terminal control sequence.
- Normal Unicode heading text and outline navigation remain unchanged.

### R6 — Medium: PDF magic bytes are currently a standalone classifier decision

Affected files:
- `src/htail_app/text_safety.py`
- `tests/test_safe_non_text_0173.py`

**Current**

Spec 001 locks known file signatures to supplementary evidence only. `inspect_bytes()` currently appends a suspicious reason whenever the sample starts with `%PDF-`, and `suspicious` is simply `bool(reasons)`. Therefore a fully printable, strictly decodable text file whose contents happen to begin with `%PDF-` is classified as suspicious solely because of the magic prefix.

That makes the signature itself a primary/standalone classifier, contrary to the locked decision. The existing PDF-like regression combines the signature with strongly binary bytes, so it does not catch this false-positive case; there is also no separate generic-binary test independent of the PDF signature.

**Target**

Make format signatures supplementary only: a magic prefix may strengthen/report a decision supported by decode/control/printability evidence, but must not by itself make otherwise readable text suspicious. Keep the classifier deterministic and bounded.

**Acceptance criteria**

- Strictly decodable, ordinary printable text beginning with `%PDF-` is not suspicious when no other binary/readability signal is present.
- PDF-like binary content remains suspicious based on the combined content signals.
- Add a generic binary/random-like regression independent of the PDF signature.
