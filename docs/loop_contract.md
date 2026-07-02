# Automatic Update Loop Contract

## Purpose

The automatic update Loop is a deterministic coordinator around the existing
`LearningLoop`, `CandidateStore`, `ReviewGate`, and `MemoryStore`. It advances a
run from task execution to measured reuse without becoming a new smart Agent.

It may detect durable artifacts, create reviewable candidates through the
existing learning loop, observe human review decisions, schedule a declared
verification task, compare outcomes, and recover from interruption.

It must never:

- accept or reject a candidate
- bypass `ReviewGate`
- write active memory directly
- modify source code
- invent a verification task
- retry model execution in an internal loop

Each `advance` invocation performs one idempotent pass. Recovery happens only
when the same command is invoked again and durable artifacts are re-read.

## Authoritative state

Each baseline run has one local state file:

```text
$STATE_DIR/learning_runs/<run_id>/auto_loop.json
```

`auto_loop.json` is the authority for coordinator state and state history. The
existing per-run files remain the authority for task evidence and outcomes:

```text
run.json
context.json
result.md
evidence.json
outcome.json
reflection.md
policy_suggestions.md
review_decisions.json
memory_rules.md
comparison.json
comparison.md
```

SQLite remains a rebuildable local index. The coordinator does not introduce a
new database or write directly to SQLite. Candidate creation, review, active
memory promotion, and reindexing continue through the existing stores and
`ReviewGate`.

## State machine

```text
task_created
→ context_built
→ execution_completed
→ outcome_recorded
→ reflection_created
→ candidate_created
→ awaiting_review
→ accepted / rejected
→ memory_activated
→ verification_scheduled
→ verified / verification_failed
```

When the outcome produces no candidates, `candidate_created → verified` is the
only no-update terminal shortcut. It records
`verification_reason=no_update_required` and performs no memory mutation.

| State | Durable trigger or output | SQLite effect | Re-entry | Active-memory mutation |
|---|---|---|---|---|
| `task_created` | validated task digest in `auto_loop.json` | none | allowed | forbidden |
| `context_built` | `context.json` | read-only search/index use | allowed | forbidden |
| `execution_completed` | `result.md` and `evidence.json` | none | allowed | forbidden |
| `outcome_recorded` | `outcome.json` | none | allowed | forbidden |
| `reflection_created` | `reflection.md` | none | allowed | forbidden |
| `candidate_created` | `policy_suggestions.md` and stable candidate IDs | candidate records may be indexed | allowed | forbidden |
| `awaiting_review` | unresolved IDs in `review_decisions.json` | candidate state only | allowed | forbidden |
| `accepted` | every candidate decided and at least one accepted | Review Gate has applied decisions | allowed | coordinator forbidden |
| `rejected` | every candidate rejected | rejected archive/index only | terminal | forbidden |
| `memory_activated` | every accepted ID is confirmed `active` by `MemoryStore` | existing Review Gate/reindex path only | allowed | only the prior Review Gate action may mutate |
| `verification_scheduled` | immutable verification-task digest and task body | none | allowed | forbidden |
| `verified` | passing `comparison.json` or no-update terminal reason | none | terminal | forbidden |
| `verification_failed` | failing `comparison.json` | none | terminal | forbidden |

## Review boundary

The coordinator stops at `awaiting_review`. A human or an explicitly authorized
caller must use the existing `review` command. On the next `advance`, the
coordinator observes `review_decisions.json`:

- all rejected → `rejected`
- at least one accepted and all decided → `accepted`
- accepted IDs confirmed active → `memory_activated`
- any undecided candidate → remain `awaiting_review`

The coordinator has no API for making review decisions.

## Verification boundary

Verification is scheduled only when the caller supplies a complete task with:

- a different `run_id`
- `baseline_run_id` equal to the baseline automatic-loop run ID
- the same workspace and project partition

The task and its digest are stored in `auto_loop.json`. If execution is
interrupted, a later `advance` resumes from `verification_scheduled` using that
same task. A changed verification task is rejected.

## Recovery and audit

Every transition is appended once to `state_history` with a UTC timestamp and
its source artifact. Failures are appended to `error_history`; the last durable
state is retained. Existing task and candidate IDs remain stable, so replay does
not duplicate execution, candidates, review decisions, or memory activation.

The audit chain is:

```text
task digest
→ context.json
→ evidence.json
→ outcome.json
→ reflection.md
→ policy_suggestions.md
→ candidate IDs
→ review_decisions.json
→ active memory IDs
→ verification task digest
→ comparison.json
```

## CLI

Initial execution and candidate generation:

```bash
python3 tools/learning_loop.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --work-root "$REPO_ROOT" \
  advance \
  --task-file config/baseline-task.json \
  --model-config config/model_backend.json
```

After explicit human review, schedule or resume verification:

```bash
python3 tools/learning_loop.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --work-root "$REPO_ROOT" \
  advance \
  --task-file config/baseline-task.json \
  --verification-task-file config/verification-task.json \
  --model-config config/model_backend.json
```

Inspect coordinator state:

```bash
python3 tools/learning_loop.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --work-root "$REPO_ROOT" \
  auto-status --run-id <baseline-run-id>
```
