# Stage 11 — Deterministic Reflection Agent

Date: 2026-07-01
Decision: PASS

## Correction made during Stage 11

The first implementation only parsed `reflection.md` and returned an in-memory result. It was not integrated into the runtime Registry and did not publish a durable reflection artifact. That version was rejected as incomplete before commit.

The final Stage 11 implementation corrects both problems:

- `loop.reflect` is registered in the v0.9 runtime and callable through `src/laos.py`.
- the Agent atomically publishes a deterministic `reflection_result.json` artifact in the local Loop run directory.

## Runtime integration

The released v0.8 five-Agent registry remains unchanged in `src/agents/registry.yaml` for compatibility and audit history.

The v0.9 runtime uses `src/agents/registry-v0.9.yaml`, which adds:

- `reflection_agent`
- class `ReflectionAgent`
- handle `loop.reflect`

`src/laos.py` builds the v0.9 registry with six Agents and gives the Reflection Agent the already validated local state directory from `CandidateStore`.

`loop.reflect` is a context-free local-state task. The Context Agent therefore supplies an empty context rather than requiring a personal or work memory workspace.

## Input

```json
{
  "type": "loop.reflect",
  "input": {
    "run_id": "<32 lowercase hexadecimal characters>"
  }
}
```

The Agent loads the run through the existing validated Loop loader. This preserves Stage 10 run ID, path, symlink, completeness, and v1/v2 contract checks.

## Durable output

The Agent writes:

```text
<state-dir>/loop_engineering/runs/<run_id>/reflection_result.json
```

The artifact contract is:

```text
laos.reflection-result.v1
```

and is defined in:

```text
schemas/reflection-result.schema.json
```

The artifact contains:

- `schema_version`
- `contract_id`
- `run_id`
- SHA-256 of the source `reflection.md`
- structured reflection output

Repeated execution with unchanged evidence reuses the existing artifact and returns `reused: true`. If the existing artifact is malformed or conflicts with current evidence, the Agent fails closed instead of overwriting it.

## Structured reflection

The structured result contains:

- `what_happened`
- `what_worked`
- `what_failed`
- `likely_cause`
- `reusable_lesson`
- typed evidence entries
- `unknown_fields`

Evidence is read only from the fixed sections in `reflection.md`:

- Task
- Outcome
- Result Evidence
- Error Evidence
- What Worked
- What Failed
- Root Cause
- Next Change

Malformed, duplicated, incomplete, or outcome-mismatched documents are rejected.

## Evidence boundary

The Agent is deterministic and does not pretend to perform unsupported autonomous reasoning.

A field becomes `unknown` when the corresponding evidence is:

- absent
- empty
- `None`
- `Pending human or agent review.`

For a failed run, explicit error evidence is used for `what_happened` before result evidence. Error evidence is not promoted to `likely_cause` unless an explicit Root Cause value exists.

This is the intended v0.9 lightweight reflection boundary: structure and preserve supplied evidence without inventing facts. Model-assisted causal analysis remains outside Stage 11.

## Safety boundaries

The Reflection Agent:

- writes only `reflection_result.json` inside the validated local run directory
- does not modify `run.json`, `reflection.md`, or `policy_suggestions.md`
- does not create memory candidates
- does not approve policies
- does not write `memory_rules.md`
- does not write or activate authoritative memory
- does not execute shell commands
- does not retry tasks
- returns `applied: false`
- always returns `requires_review: true`

## Verification

The Stage 11 tests cover:

1. deterministic artifact publication and reuse
2. fail-run error priority without invented cause
3. missing evidence mapped to `unknown`
4. invalid run IDs, malformed tasks, outcome mismatch, and artifact conflict rejection
5. real Loop v2 integration with no memory or rule side effects
6. v0.9 Registry and CLI routing for `loop.reflect`
7. Reflection Result JSON Schema identity and required fields

Final regression after the correction:

```text
333 tests passed
```

## Next stage

Stage 12 is Policy Learning. It should consume the durable `reflection_result.json` artifact rather than reparsing free-form task output. It may create policy suggestions only. Human confirmation remains the only route into `memory_rules.md`, and approved rules still must not directly change active memory or Agent behavior.
