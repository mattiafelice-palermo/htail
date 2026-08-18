# Implementer/Reviewer Spec Workflow

This folder contains a lightweight two-agent workflow for implementing specs.

The workflow deliberately separates three responsibilities:

- `docs/specs/NNN-agent-state.json` — the **single source of truth for turn/state**;
- `docs/specs/NNN-agent-coordination.md` — the **timestamped communication log** between agents;
- `docs/specs/reviews/...-review.md` — the **canonical technical review**, including `R1`, `R2`, etc.

The specs define what to build. Repository-level agent guidance defines how the code should be changed; this workflow does not replace it.

## 1. Complete workflow

The **reviewer initializes the workflow** after the feature branch and parent/child specs exist.

```text
REVIEWER initializes workflow
        ↓
IMPLEMENTER implements ACTIVE_CHILD
        ↓
IMPLEMENTER verifies + hands off
        ↓
REVIEWER reviews actual code/tests
        │
        ├── problems
        │     ↓
        │   reviewer writes R1/R2/... in canonical review file
        │     ↓
        │   reviewer hands those findings to IMPLEMENTER
        │     ↓
        │   IMPLEMENTER fixes only those findings
        │     ↓
        │   IMPLEMENTER verifies + hands back
        │     ↓
        │   REVIEWER re-reviews
        │     └──────── repeat until clean
        │
        └── child clean
               ↓
            another child?
             ├── yes → IMPLEMENTER implements next child
             └── no  → REVIEWER performs cumulative parent review
                              │
                              ├── problems → same fix/review loop
                              ├── clean + all required evidence available → COMPLETE
                              └── clean + required external dependency unavailable → BLOCKED
                                                                                     ↓
                                                                           later resume final review
```

The remote Git branch is authoritative. A new agent should be able to resume by reading the state file, recent coordination entries, active spec, and canonical review file.

## 2. Reviewer initializes the workflow

From the repository root, the reviewer initializes Spec `NNN` with the equivalent of:

```bash
python docs/specs/workflow/spec_workflow.py init NNN
```

This creates:

```text
docs/specs/NNN-agent-state.json
docs/specs/NNN-agent-coordination.md
```

Child specs named `NNN.1-*.md`, `NNN.2-*.md`, etc. are discovered automatically.

Initial state:

```text
TURN: IMPLEMENTER
ACTION: IMPLEMENT
ACTIVE_CHILD: first child
```

Initialization also appends the first timestamped reviewer → implementer communication entry. Commit/push both workflow files together.

## 3. JSON state: turn/state only

The JSON file is the only authority for whose turn it is.

Important fields:

- `active_child` — the only work unit currently allowed;
- `turn` — `IMPLEMENTER` or `REVIEWER`;
- `action` — what that role must do;
- `findings` — unresolved canonical review finding IDs;
- `resume_review` — internal state used to return fixes to ordinary or final review.

Normal states:

```text
IMPLEMENTER + IMPLEMENT
IMPLEMENTER + FIX_REVIEW
REVIEWER    + REVIEW
REVIEWER    + FINAL_REVIEW
REVIEWER    + BLOCKED
REVIEWER    + COMPLETE
```

`BLOCKED` is terminal for the current agent sessions but resumable later. It means the implementation/review is clean enough that no implementer finding remains, but a required external dependency or acceptance input is unavailable, so the feature is not complete or merge-ready.

Do not put prose communication in the JSON and do not edit it manually when the workflow script is available.

## 4. Coordination Markdown: communication between agents

`docs/specs/NNN-agent-coordination.md` is append-only from the agents' perspective. The workflow script appends one entry on every transition.

Every entry includes an ISO-8601 timestamp with UTC offset.

### Implementer → reviewer

```markdown
### 2026-01-01T00:00:00+00:00 — IMPLEMENTER → REVIEWER — NNN.1

**Result:** Implementation ready

**Verification**

- focused tests: PASS
- repository validation: PASS
- browser checks: NOT RUN

**Message**

The message briefly summarizes the sensitive area touched.
```

For review fixes, the result is `Review fixes ready`.

The implementer supplies verification with repeated `--verification` options and may add one concise `--message`.

### Reviewer → implementer

```markdown
### 2026-01-01T00:05:00+00:00 — REVIEWER → IMPLEMENTER — NNN.1

**Result:** Changes required

**Findings**

- R1
- R2

**Message**

R1 is the functional blocker. R2 is isolated.
```

Keep messages short. Detailed defects belong in the canonical review file.

## 5. Always start a turn by reading state + communication

Run:

```bash
python docs/specs/workflow/spec_workflow.py status
```

Then read the latest entries in:

```text
docs/specs/NNN-agent-coordination.md
```

Act only when `TURN` matches your role. Work only on `ACTIVE_CHILD`.

If `ACTION: BLOCKED` or `ACTION: COMPLETE`, stop the current agent session.

## 6. Implementer workflow

### IMPLEMENT

When state is `IMPLEMENTER + IMPLEMENT`, implement only `ACTIVE_CHILD`.

### FIX_REVIEW

When state is `IMPLEMENTER + FIX_REVIEW`, read the canonical review file and fix only the `R` IDs listed in state.

For each finding, satisfy:

- **Current** — what is wrong;
- **Target** — what it must become;
- **Acceptance criteria** — what proves resolution.

The implementer does not edit, renumber, delete, or self-resolve reviewer findings.

### Verification efficiency — mandatory sequence

During implementation and review-fix work, use focused checks for the changed area. Before a normal handoff, use this sequence:

```text
focused tests/checks required by the active spec/review
→ other focused checks such as compile or diff checks when relevant
→ the repository's canonical aggregate validation, when one exists
→ handoff
```

Therefore:

- do not duplicate a complete suite immediately before canonical validation;
- run an additional full-suite command only when the active spec, reviewer finding, or user explicitly requires it;
- diagnose failures with the narrowest relevant test or check first.

Follow the repository's own agent guidance for exact validation commands and scope. When a canonical aggregate check covers the relevant area, treat it as the full-suite evidence for the handoff.

If no canonical aggregate command exists, run the complete validation required by repository guidance before handoff. Explicit acceptance, packaging, browser, manual, or other task-specific verification remains mandatory.

### Handoff

After verification:

```bash
python docs/specs/workflow/spec_workflow.py handoff-review \
  --verification "focused tests: PASS" \
  --verification "repository validation: PASS" \
  --verification "browser checks: NOT RUN" \
  --message "Optional concise context."
```

Then stage:

- implementation changes;
- `NNN-agent-state.json`;
- `NNN-agent-coordination.md`.

Commit them together and push once.

**After pushing the handoff, the implementer must stop repository work completely.** Do not begin the next child, do not make speculative fixes, and do not continue editing while `TURN: REVIEWER`. Wait until the reviewer commits/pushes a new state with `TURN: IMPLEMENTER`; only then resume from the new `ACTION` and `FINDINGS`. If the reviewer instead commits `ACTION: BLOCKED` or `ACTION: COMPLETE`, stop the current implementer session.

## 7. Review files and exact naming

Canonical reviews live under:

```text
docs/specs/reviews/
```

The review filename must mirror the corresponding spec filename exactly, replacing `.md` with `-review.md`.

Examples:

```text
docs/specs/NNN.1-feature.md
→ docs/specs/reviews/NNN.1-feature-review.md

docs/specs/NNN-feature-parent.md
→ docs/specs/reviews/NNN-feature-parent-review.md
```

Therefore:

- each child gets its own corresponding review file;
- the final cumulative parent review uses the review filename corresponding to the parent spec itself;
- subsequent review rounds update the same review file rather than creating `-review-2`, `-v2`, etc.

## 8. Reviewer and R findings

Each actionable finding must use:

```markdown
### R1 — High | Medium | Low: concise title

Affected files:
- `path/to/file`

**Current**

Exact defect/spec deviation/regression risk.

**Target**

Required behavior or implementation.

**Acceptance criteria**

- Specific testable condition.
- Required regression coverage/verification where relevant.
```

The reviewer owns finding creation, numbering, updates, and resolution.

### Changes required

After writing/updating the review file:

```bash
python docs/specs/workflow/spec_workflow.py request-fixes R1 R2 \
  --message "Optional concise context."
```

Commit/push review + JSON state + coordination together.

### Returned fixes

Re-check every open finding against its Target and Acceptance criteria.

If some remain open, return only those IDs. If all are resolved and no new actionable defects exist, mark the child review clean.

### Review clean

```bash
python docs/specs/workflow/spec_workflow.py review-clean \
  --message "Optional concise context."
```

The script either advances to the next child or enters `FINAL_REVIEW`.

Commit/push review + JSON state + coordination together.

## 9. Final parent review

When state is:

```text
TURN: REVIEWER
ACTION: FINAL_REVIEW
```

perform a fresh cumulative review against the correct merge base.

Check complete branch scope, all locked parent requirements, cumulative regressions, final architecture/ownership, required verification, documentation/status closure, and merge readiness.

Use the same R-finding loop if implementation defects or agent-actionable verification gaps exist.

### Clean and complete

When the cumulative review is clean and all required acceptance evidence is available:

```bash
python docs/specs/workflow/spec_workflow.py complete \
  --message "Cumulative parent review clean; feature ready to merge."
```

Commit/push final review + JSON state + coordination together.

Final state:

```text
TURN: REVIEWER
ACTION: COMPLETE
```

Both agents stop.

### Clean but externally blocked

If no implementation finding remains, but a required external dependency or acceptance input is unavailable, record the exact reason in the parent review and use:

```bash
python docs/specs/workflow/spec_workflow.py block \
  --message "Exact external dependency preventing completion."
```

This transitions to:

```text
TURN: REVIEWER
ACTION: BLOCKED
```

`BLOCKED` means the feature is not complete or merge-ready, but neither agent has productive repository work to do. Commit/push the parent review + JSON state + coordination together, then both agents stop polling and stop their current sessions.

Do not search unrelated user storage, previous uploads, File Library, or other sources to satisfy the missing external gate unless the user explicitly asks for that search or identifies the source to use.

When the required external dependency later becomes available:

```bash
python docs/specs/workflow/spec_workflow.py resume-final-review \
  --message "Required external dependency is now available."
```

This returns the state to:

```text
TURN: REVIEWER
ACTION: FINAL_REVIEW
```

Commit/push the resumed JSON state + coordination entry, then perform the cumulative final review with the newly available evidence. Do not transition directly from `BLOCKED` to `COMPLETE`.

## 10. Important rules

- Reviewer initializes the workflow.
- JSON is the sole authority for turn/action.
- Coordination Markdown is append-only communication history.
- Review Markdown is the canonical technical review.
- Always read state and latest communication before acting.
- Work only on `ACTIVE_CHILD`.
- Implementer does not edit reviewer findings.
- Reviewer does not modify implementation code unless explicitly instructed.
- After implementer handoff/push, the implementer waits and does no repository work until `TURN: IMPLEMENTER` returns.
- `BLOCKED` is only for a clean final review that cannot complete because a required external dependency/acceptance input is unavailable; it is not a substitute for ordinary review findings.
- While `BLOCKED`, neither agent polls or performs speculative work. Resume only through `resume-final-review` when the external dependency is actually available.
- Transition state before the handoff commit.
- Commit substantive work + state + coordination together.
- Push once, then stop when ownership changes.
- Never invent verification results.

## 11. Command summary

```text
python docs/specs/workflow/spec_workflow.py init NNN [--message "..."]
python docs/specs/workflow/spec_workflow.py status
python docs/specs/workflow/spec_workflow.py handoff-review \
  [--verification "..."] [--verification "..."] [--message "..."]
python docs/specs/workflow/spec_workflow.py request-fixes R1 R2 ... [--message "..."]
python docs/specs/workflow/spec_workflow.py review-clean [--message "..."]
python docs/specs/workflow/spec_workflow.py block --message "..."
python docs/specs/workflow/spec_workflow.py resume-final-review [--message "..."]
python docs/specs/workflow/spec_workflow.py complete [--message "..."]
```
