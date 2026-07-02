# Stage 07 — End-to-End Learning Loop

Stage 07 proves one bounded claim:

> A reviewed task lesson can enter active memory, be injected into a later task,
> be cited by that task, and measurably improve the declared outcome.

It does not add another input channel, model adapter, vector store, or autonomous
memory writer.

## Closed-loop contract

```text
task JSON
→ active-memory Context Builder
→ model execution over explicit evidence files
→ run.json + result.md + evidence.json + outcome.json
→ reflection.md + policy_suggestions.md
→ Memory candidate
→ human Review Gate
→ active memory or rejected archive
→ second task
→ comparison.json + comparison.md
```

Active memory is never modified by `run`. Only the explicit `review` command can
call the existing Review Gate.

## Run states

```text
created
→ context_ready
→ running
→ completed / failed / interrupted
→ reflected
→ review_pending / completed
→ reviewed
→ verified / verification_failed
```

Artifacts are written atomically. Re-running the same `run_id` with the same
task resumes missing steps and reuses stable candidate source IDs. Reusing a
`run_id` for a different task is rejected.

## Required task fields

```json
{
  "run_id": "stage07-context-audit-1",
  "task_id": "context-builder-audit",
  "type": "repository.module_audit",
  "title": "Audit Context Builder",
  "instruction": "Audit the module and cite concrete evidence.",
  "query": "idempotency fail-closed recovery context audit",
  "workspace": "personal",
  "confidentiality": "personal",
  "inputs": ["src/context/builder.py"],
  "minimum_score": 1.0,
  "context_limit": 8000,
  "criteria": [
    {
      "id": "fail_closed",
      "description": "verify fail-closed behavior",
      "required_terms": ["fail-closed"],
      "strategy": "For module audits, verify fail-closed behavior before declaring a capability available."
    }
  ]
}
```

Input paths must be relative to `--work-root`. Restricted tasks are rejected
before a model call. Restricted memories are already excluded by `MemoryStore`
and are checked again before a context artifact is accepted.

## First run

```bash
python3 tools/learning_loop.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --work-root "$REPO_ROOT" \
  run \
  --task-file config/stage07_module_audit_run1.example.json \
  --model-config config/model_backend.example.json
```

Each run is stored under:

```text
$STATE_DIR/learning_runs/<run_id>/
├── run.json
├── context.json
├── result.md
├── evidence.json
├── outcome.json
├── reflection.md
└── policy_suggestions.md
```

A failed criterion becomes a stable Memory candidate. Replaying the run does not
create a duplicate candidate.

## Human review

Read `policy_suggestions.md` and review every candidate explicitly:

```bash
python3 tools/learning_loop.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --work-root "$REPO_ROOT" \
  review \
  --run-id stage07-context-audit-1 \
  --candidate-id principle-YYYYMMDD-XXXXXXXX \
  --action accept \
  --reason "Reusable repository-audit strategy"
```

Use `--action reject` for a suggestion that should not enter active memory.
Review decisions create:

```text
review_decisions.json
memory_rules.md
```

Only accepted candidates become active. Rejected candidates remain unavailable
to Context Builder.

## Second run and comparison

Run a structurally similar task with a different `run_id`, input module, and
`baseline_run_id`. The model is instructed to list exact memory IDs under
`Memory used:`.

```bash
python3 tools/learning_loop.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --work-root "$REPO_ROOT" \
  run \
  --task-file config/stage07_module_audit_run2.example.json \
  --model-config config/model_backend.example.json
```

Then compare:

```bash
python3 tools/learning_loop.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --work-root "$REPO_ROOT" \
  compare \
  --first-run stage07-context-audit-1 \
  --second-run stage07-projector-audit-2 \
  --minimum-improvement 0.2
```

Stage 07 passes only when all checks are true:

1. The score improves by the declared threshold.
2. At least one accepted candidate is present in the second context.
3. The second result cites that exact memory ID.
4. Rejected candidates are absent from the second context.
5. Restricted memories are absent from the second context.
6. The second run has no more failed criteria than the first.

A completed workflow with no measured improvement is recorded as
`verification_failed`, not as success.

## Stage 07.1 — non-empty exclusion acceptance

A basic empty-list assertion can pass even when no rejected or restricted record
was relevant to the second task. Stage 07.1 adds an adversarial acceptance mode
that proves both exclusion paths with real, query-relevant records in the same
workspace and project scope.

```bash
python3 tools/learning_loop.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --work-root "$REPO_ROOT" \
  compare \
  --first-run stage07-context-audit-1 \
  --second-run stage07-projector-audit-2 \
  --minimum-improvement 0.2 \
  --require-nonempty-exclusion-evidence
```

When the flag is enabled, `comparison.json` must contain non-empty
`rejected_match_ids` and `restricted_match_ids`, while `rejected_in_context` and
`restricted_sources` remain empty. The comparison also records these explicit
checks:

- `rejected_strategy_challenged`
- `restricted_memory_challenged`
- `rejected_strategy_excluded`
- `restricted_memory_excluded`

This mode does not weaken workspace, project, confidentiality, or Review Gate
rules. It only strengthens acceptance evidence.

## CI acceptance coverage

The integration suite uses deterministic module-audit tasks to verify:

- no automatic active-memory mutation
- stable candidate IDs and replay deduplication
- explicit accept and reject decisions
- accepted strategy injection and attribution
- rejected strategy isolation
- restricted-memory exclusion
- non-empty rejected and restricted challenge evidence
- interruption recovery without partial artifacts
- evidence-to-candidate-to-review-to-memory provenance
- measured first-run versus second-run improvement
