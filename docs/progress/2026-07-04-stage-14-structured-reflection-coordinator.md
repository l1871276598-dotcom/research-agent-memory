# Stage 14 — Structured Reflection Evidence and Auto-update Loop Coordinator

Date: 2026-07-04
Decision: PASS

## Scope

Stage 14 was implemented in two ordered parts:

1. Stage 14.1 adds explicit structured Reflection evidence so a real failed Loop can produce a verifiable `reusable_lesson` without inventing missing facts.
2. The lightweight Coordinator invokes the already implemented Loop stages in order and preserves every existing review, conflict, evidence, and activation gate.

This stage does not add a background watcher, task executor, retry engine, automatic policy approval, automatic candidate acceptance, active-memory promotion, or a large autonomous coordinator.

## Stage 14.1 — structured Reflection evidence

The existing `finalize_memory()` interface now accepts two optional evidence fields:

- `root_cause`
- `next_change`

The CLI exposes the same fields as:

```text
--root-cause
--next-change
```

When supplied, the values are written into the fixed `Root Cause` and `Next Change` sections of `reflection.md`. The existing Reflection Agent then deterministically maps them to:

- `likely_cause`
- `reusable_lesson`

No causal inference or lesson generation occurs inside the Agent. Missing, empty, or placeholder evidence still becomes `unknown`.

A real vertical failed-Loop test verifies this path:

```text
finalize
→ reflection.md
→ reflection_result.json
→ reusable_lesson
→ policy_candidates.json
```

The resulting policy remains `review_required` and `applied: false`.

## Idempotency compatibility

Explicit structured evidence participates in the Loop idempotency digest. Therefore changing `root_cause` or `next_change` creates a different run identity.

For backward compatibility, when both new fields are absent, they are omitted from the digest payload. Existing Stage 10 v2 requests therefore retain their previous deterministic run IDs.

## Lightweight Loop Coordinator

The v0.9 Registry now includes:

- `agent_id: loop_coordinator_agent`
- class `LoopCoordinatorAgent`
- handle `loop.coordinate`

The Coordinator accepts completed task evidence and an explicit destination partition. Required inputs are:

- `task`
- `result`
- `outcome`
- `workspace`

Optional inputs are:

- `error`
- `reflection`
- `root_cause`
- `next_change`
- `project`

Unknown inputs, including approval or activation switches, are rejected.

## Coordinated flow

The Coordinator invokes existing stages in this exact order:

```text
finalize
→ reflect
→ suggest policies
→ evaluate low-risk candidate generation
```

It does not duplicate the underlying business rules. The existing components remain responsible for:

- Loop run validation and idempotency
- structured evidence parsing
- exact duplicate and explicit conflict classification
- the minimum three-independent-evidence threshold
- deterministic candidate generation and two-phase recovery
- candidate-only Memory Core writes
- Review Gate ownership of activation

The Coordinator validates the Policy artifact path inside the local state directory before reading stable policy IDs. Each policy is then evaluated through the existing Low-risk Candidate Agent.

## Idempotent re-entry

The Coordinator does not introduce a second state machine. Re-running the same request safely re-enters the existing durable stages:

- `finalize` reuses the same Loop run
- Reflection reuses the same `reflection_result.json`
- Policy Learning reuses the same policy artifacts
- Candidate Generation reuses the same request artifact and candidate ID

A three-run vertical test verifies that the third independent task/result fingerprint creates exactly one principle candidate and that a repeated Coordinator call creates no duplicate.

## Review and safety boundaries

The Coordinator never:

- checks or approves policy review boxes
- writes `memory_rules.md`
- invokes Review Gate acceptance
- changes a candidate to active
- lowers the three-evidence threshold
- resolves policy conflicts semantically
- guesses workspace or project ownership
- executes the represented task
- retries a failed task
- runs continuously or watches directories

Generated principle records remain `status: candidate` and require explicit Review Gate action.

## Verification

Stage 14 tests cover:

1. a real failed Loop produces explicit `likely_cause` and `reusable_lesson`
2. the reusable lesson becomes one reviewable policy candidate
3. missing `next_change` remains `unknown`
4. explicit structured evidence changes run identity
5. absent structured evidence preserves the Stage 10 v2 identity
6. a single coordinated failure stops on insufficient independent evidence
7. three independent failures create exactly one review candidate
8. repeated coordination reuses all durable stages
9. CLI routing works and approval-like input is rejected
10. no policy registry or active memory is created automatically

Final local verification:

```text
355 tests passed
compileall passed
git diff --check passed
git diff --cached --check passed
```

## Next stage

Stage 15 is the v0.9 architecture and local-security audit. It should independently review the complete Stage 10–14 diff, especially path containment, artifact consistency, Review Gate preservation, and the boundary between the lightweight Coordinator and future large autonomous coordination.
