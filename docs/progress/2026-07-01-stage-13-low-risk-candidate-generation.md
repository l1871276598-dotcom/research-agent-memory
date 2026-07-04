# Stage 13 — Low-risk Candidate Generation

Date: 2026-07-01
Decision: PASS
Stage 15 hardening update: 2026-07-04

## Scope

Stage 13 adds a conservative Agent that aggregates repeated Loop evidence and creates a Memory Core `principle` candidate only after a fixed threshold is met. It does not create active memory, approve policies, resolve conflicts, change Agent behavior, or execute commands.

## Runtime integration

The v0.9 Registry contains the Low-risk Candidate Agent:

- `agent_id: low_risk_candidate_agent`
- class `LowRiskCandidateAgent`
- handle `loop.generate-candidate`

The Agent is available through `src/laos.py` and is treated as a context-free local-state task.

## Input

```json
{
  "type": "loop.generate-candidate",
  "input": {
    "policy_id": "<16 lowercase hexadecimal characters>",
    "workspace": "personal"
  }
}
```

An optional `project` may be supplied. The caller must provide the destination workspace explicitly. New partition-aware v2 Loop runs record `workspace` and `project` provenance, and Stage 13 aggregates only evidence whose partition exactly matches the requested candidate partition.

Legacy Stage 10 v2 runs that predate partition provenance remain readable and are interpreted as `personal` workspace with global scope. They cannot contribute to work or project-scoped candidate generation.

Partition mapping is deterministic:

- personal workspace → personal confidentiality
- work workspace → internal confidentiality
- no project → global scope
- project supplied → project scope and existing project validation through Memory Core

## Evidence threshold

A candidate is generated only when the same policy has at least three independent, reviewable Loop evidence records in the same workspace and project partition.

A qualifying evidence record must have:

- a valid v2 Loop run
- matching workspace and project provenance
- a valid Stage 11 `reflection_result.json`
- a valid Stage 12 `policy_candidates.json`
- deterministic Reflection reconstruction matching `run.json` and `reflection.md`
- the requested stable policy ID
- policy status `review_required`
- no duplicate target
- no conflict references
- valid task and result SHA-256 values

Independence is not based only on different run IDs. Stage 13 computes a stable evidence fingerprint from the run's task and result hashes. Multiple runs with the same task/result pair count only once, even if unrelated fields changed the run ID.

The threshold is fixed at:

```text
3 independent task/result fingerprints
```

The task cannot lower or override this threshold.

## Blocking rules

Stage 13 creates no Memory Core principle candidate when:

- fewer than three independent evidence records exist
- evidence belongs to another workspace or project
- the policy is already an exact approved-rule duplicate
- an explicit policy conflict exists
- the Policy artifact is malformed or inconsistent
- the Reflection artifact cannot be reconstructed from source evidence
- a run path or artifact path is unsafe
- the policy ID does not match the normalized policy text
- a reused generation intent no longer matches current validated evidence

Insufficient evidence returns a normal, non-writing result. Malformed, forged, stale, or tampered evidence fails closed.

## Generated Memory candidate

When the threshold is met, the Agent calls the existing Memory Core candidate interface. It creates:

- type: `principle`
- status: `candidate`
- confidence: `inferred`
- source: `loop-engineering:stage13`
- explicit workspace and confidentiality
- Loop run IDs as evidence
- stable policy and generation request references
- a content warning that Review Gate approval is still required

The Agent result includes the real candidate ID in the BaseAgent `candidates` list.

No direct file writer is used for authoritative memory. Candidate creation remains delegated to Memory Core and the existing candidate backend.

## Review boundary

The candidate is not active after Stage 13.

Only the existing Review Gate may transition it. Stage 13 does not:

- invoke Review Gate automatically
- accept or reject the candidate
- write an active memory record
- mark the policy approved
- change `memory_rules.md`
- change prompts or Agent behavior

After later human review, repeated Stage 13 requests reuse the same generation record and report the current memory lifecycle state instead of creating another candidate.

## Two-phase recovery

The local generation artifact is written under:

```text
<state-dir>/loop_engineering/generated_candidates/<request_id>.json
```

Its contract is:

```text
laos.low-risk-candidate.v1
```

The machine-readable Schema is:

```text
schemas/low-risk-candidate.schema.json
```

The deterministic request ID is derived from:

- policy ID
- workspace
- project

Generation uses two states:

1. `pending_creation`
2. `candidate`

The intent artifact is written before Memory Core candidate creation. If execution stops after the candidate is created but before the artifact is completed, a retry uses the same deterministic candidate content and request reference. The existing Memory Core deduplication returns the same candidate, allowing the artifact to recover without generating a duplicate.

Before either recovery or reuse, Stage 13 revalidates the complete evidence set. Recorded run IDs and task/result fingerprints must still exist, remain reviewable, stay in the same partition, and continue to satisfy the fixed threshold.

A completed artifact records:

- request and policy IDs
- normalized policy text
- workspace and project
- fixed threshold
- evidence run IDs
- independent evidence fingerprints
- candidate ID and path
- `applied: false`

## Idempotency

Repeating the same eligible request:

- reuses the same request artifact
- revalidates current evidence
- returns the same candidate ID
- does not create a second principle candidate
- reports the current memory status

Changing workspace or project creates a different deterministic request because it targets a different memory partition.

## Safety boundaries

Stage 13 guarantees:

- no single-run promotion
- no threshold override
- no cross-workspace or cross-project evidence reuse
- no fuzzy or semantic evidence merging
- no duplicate or conflicted-policy promotion
- no inferred workspace
- no direct active-memory write
- no automatic Review Gate action
- no automatic policy approval
- no shell execution
- no automatic retries beyond deterministic caller retry
- all durable runtime coordination artifacts remain in the local state directory

## Verification

The Stage 13 and Stage 15 regression set covers:

1. fewer than three independent runs produces no principle candidate
2. zero runs returns normal insufficient evidence
3. different run IDs with the same task/result fingerprint count only once
4. three independent runs create exactly one candidate and repeated calls reuse it
5. interrupted two-phase publication recovers the same candidate
6. forged pending intents cannot bypass the evidence threshold
7. coordinated Reflection and Policy tampering fails closed
8. duplicate policy status blocks candidate generation
9. work evidence cannot generate personal candidates
10. project evidence cannot cross project or global boundaries
11. real CLI routing creates a work/internal candidate that remains awaiting review

Two Schema tests verify:

- contract identity and fixed threshold
- exact required fields and two-phase state values

Final Stage 15 regression:

```text
364 tests passed
```

## Next stage

Stage 14 provides the lightweight Auto-update Loop Coordinator:

```text
finalize → reflect → suggest policies → evaluate low-risk candidate
```

It preserves every existing gate, stops safely on missing evidence or conflicts, resumes idempotently, and never includes automatic candidate acceptance or active-memory promotion.
