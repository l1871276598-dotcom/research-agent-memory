# Stage 12 — Policy Learning

Date: 2026-07-01
Decision: PASS

## Scope

Stage 12 adds a deterministic Policy Agent that consumes the durable Stage 11 reflection artifact and produces reviewable policy candidates. It does not approve policies, change Agent behavior, write active memory, execute commands, retry tasks, or apply rules automatically.

## Runtime integration

The v0.9 Registry now contains seven Agents. The new entry is:

- `agent_id: policy_agent`
- class `PolicyAgent`
- handle `loop.suggest-policies`

The Agent is available through `src/laos.py` and is treated as a context-free local-state task. It does not receive Memory Core context or restricted memory content.

## Strict input boundary

Policy Learning requires:

```text
<state-dir>/loop_engineering/runs/<run_id>/reflection_result.json
```

The input must satisfy the Stage 11 contract:

```text
laos.reflection-result.v1
```

The Agent validates:

- the Loop run is a v2 run
- the reflection artifact belongs to the requested run
- the reflection outcome matches the run outcome
- the artifact hash still matches the source `reflection.md`
- the structured reflection object is present

The legacy `policy_suggestions.md` file is not used as a Policy Learning input. It remains only as historical v0.8 compatibility data.

## Candidate generation boundary

A policy candidate is generated only from an explicit, non-`unknown` `reusable_lesson` in `reflection_result.json`.

The Agent does not automatically convert:

- task results
- error messages
- `what_worked`
- `what_failed`
- likely causes

into policy rules. This prevents observations from being silently promoted into governance instructions.

When `reusable_lesson` is `unknown`, the Agent produces an empty, valid policy artifact and reports zero candidates.

## Stable identity and exact deduplication

Policy text is whitespace-normalized. The stable policy ID is:

```text
first 16 hexadecimal characters of SHA-256(normalized policy text)
```

An exact match against an already approved rule in `memory_rules.md` is classified as:

```text
status: duplicate
```

Duplicate candidates are recorded for audit but are not shown as checkable review candidates.

No vector similarity, fuzzy text comparison, or semantic merge is performed in Stage 12.

## Explicit conflict detection

Stage 12 detects only explicit opposite directives with the same normalized subject.

Supported directive forms include:

```text
require: <subject>
forbid: <subject>
必须: <subject>
要求: <subject>
禁止: <subject>
```

Opposite directives are classified as:

```text
status: conflicted
conflicts_with: [approved rule IDs]
```

Conflicted candidates are not checkable and are never automatically resolved. General guidance text is not assigned a conflict merely because it is different from an existing rule.

## Durable outputs

The Policy Agent atomically writes:

```text
<run-dir>/policy_candidates.json
<run-dir>/policy_review.md
```

The machine-readable artifact uses:

```text
contract_id: laos.policy-candidates.v1
```

Its JSON Schema is:

```text
schemas/policy-candidates.schema.json
```

Each candidate records:

- `policy_id`
- original and normalized text
- directive effect
- normalized subject
- status
- duplicate target, when present
- explicit conflict rule IDs, when present

The review document has separate sections for:

- reviewable candidates
- exact duplicates
- explicit conflicts

Only `review_required` candidates receive unchecked review boxes.

## Idempotency and tamper behavior

Repeating `loop.suggest-policies` with unchanged reflection evidence reuses the existing artifacts and returns:

```text
reused: true
```

If either policy artifact has been altered or only one artifact exists, the Agent fails closed rather than overwriting review work.

The Loop run's `policy_suggestion_ids` field is updated atomically to the generated candidate IDs. It does not change `approved_policy_ids`, `policy_status`, or the run completion state.

## Safety boundaries

Stage 12 guarantees:

- no automatic approval
- no write to `memory_rules.md`
- no active-memory creation or activation
- no prompt or Agent behavior changes
- no semantic conflict resolution
- no command execution
- no retry loop
- `applied: false` in the policy artifact and Agent output
- BaseAgent candidate list remains empty because policy candidates are not Memory Core candidates

Human review remains mandatory. Connecting reviewed Stage 12 candidates to explicit approval is outside this stage's generation-only scope.

## Verification

Six Policy Agent tests cover:

1. strict use of `reflection_result.json` and rejection of legacy suggestion input
2. stable policy identity and repeated-run reuse
3. exact approved-rule duplicate classification
4. explicit opposite-directive conflict classification
5. missing or tampered reflection artifacts failing closed
6. real v0.9 CLI routing with zero memory and rule side effects

Two Schema tests verify the machine-readable policy contract and candidate status fields.

Final regression:

```text
341 tests passed
```

## Next stage

Stage 13 is Low-risk Candidate Generation. It should aggregate repeated, independent Loop evidence and create Memory Core candidates only when conservative thresholds are met. It must not create active memory, must not use single-run evidence as sufficient proof, and must continue routing every generated memory through Review Gate.
