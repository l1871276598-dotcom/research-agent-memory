# Stage 07.2 Real Loop Acceptance

## Goal

Stage 07.2 validates the automatic update Loop in a real local Codex environment, not only in deterministic unit tests:

`task -> context -> execution -> outcome -> reflection -> policy candidate -> human review -> memory activation -> verification task -> comparison -> verified`

The accepted scope is still a rule-based coordinator around the existing `LearningLoop`, `ReviewGate`, `CandidateStore`, and `MemoryStore`. It does not add a new autonomous planner, bypass review, or write active memory directly.

## Environment

- Acceptance date: 2026-07-02 UTC
- Branch during acceptance: `codex/stage-07-2-real-loop-acceptance`
- Git baseline from `origin/main`: `a6170abc6309086fbe6ab5081b1fc039778773be`
- Model backend type: `codex`
- Codex CLI: `codex-cli 0.142.5`
- Runtime roots: local temporary `data/` and `state/` directories outside the repository and outside iCloud-backed runtime storage
- Sanitized evidence manifest: `docs/fixtures/stage07_2_real_acceptance_summary.json`

The health probe for the real run reported `healthy=true` with no blocking items. No token, cookie, secret, personal memory content, SQLite database, or runtime log is committed.

## Acceptance Scenario

Committed fixtures:

- Baseline task: `config/stage07_2_real_acceptance_baseline.example.json`
- Verification task: `config/stage07_2_real_acceptance_verification.example.json`
- No-candidate task: `config/stage07_2_real_acceptance_no_candidate.example.json`

Key design choices:

- The real acceptance runs in `workspace=work`, `project=stage07-2-real-acceptance`.
- The baseline task is intentionally shaped to produce two reviewable candidates:
  - accepted: `fail_closed`
  - rejected: `recovery_ledger`
- A separate restricted fixture is activated through the existing `ReviewGate`, so non-empty restricted exclusion evidence is challenged in the same workspace/project partition.
- The verification task keeps the same workspace/project partition, uses a different `run_id`, points `baseline_run_id` back to the baseline run, and requires explicit memory attribution.

## Real Run Result

Baseline:

- Run ID: `stage07-2-work-baseline`
- State path: `task_created -> context_built -> execution_completed -> outcome_recorded -> reflection_created -> candidate_created -> awaiting_review`
- Score: `0.5`
- Candidate IDs:
  - accepted later: `principle-20260702-bdf1d61d`
  - rejected later: `principle-20260702-8e057f3c`
- Active memory before review: `[]`

Review Gate:

- Accepted via existing `review` command only: `principle-20260702-bdf1d61d`
- Rejected via existing `review` command only: `principle-20260702-8e057f3c`
- Restricted fixture activated through existing `ReviewGate`: `principle-20260702-3bc28da4`
- Active memory after review: `["principle-20260702-bdf1d61d"]`

Verification:

- Verification run ID: `stage07-2-work-verification`
- Baseline pointer: `baseline_run_id=stage07-2-work-baseline`
- Final coordinator path:
  `task_created -> context_built -> execution_completed -> outcome_recorded -> reflection_created -> candidate_created -> awaiting_review -> accepted -> memory_activated -> verification_scheduled -> verified`
- Verification score: `1.0`
- Comparison delta: `+0.5`
- Final state: `verified`

Comparison checks all passed:

- `minimum_improvement`
- `accepted_strategy_injected`
- `accepted_strategy_attributed`
- `rejected_strategy_challenged`
- `rejected_strategy_excluded`
- `restricted_memory_challenged`
- `restricted_memory_excluded`
- `second_run_not_worse`

## Accepted / Rejected / Restricted Outcome

- Accepted memory entered the verification context and was explicitly cited:
  `context_sources=["principle-20260702-bdf1d61d"]`
  `used_memory_ids=["principle-20260702-bdf1d61d"]`
- Rejected memory was query-relevant but remained out of context:
  `rejected_match_ids=["principle-20260702-8e057f3c"]`
  `rejected_in_context=[]`
- Restricted memory was query-relevant but remained out of context:
  `restricted_match_ids=["principle-20260702-3bc28da4"]`
  `restricted_sources=[]`

## Idempotency And Recovery

Real Codex replay checks:

- Re-running baseline `advance` while already in `awaiting_review` kept the same state, state history, candidate IDs, `auto_loop.json` digest, and `result.md` digest.
- Re-running `advance` did not auto-accept or auto-reject anything; the run remained in `awaiting_review`.
- A real no-candidate task completed as `verified` with `verification_reason=no_update_required`, `candidate_ids=[]`, and zero active-memory change.

Deterministic automated recovery checks kept alongside the real run:

- `tests/test_auto_update_loop.py` verifies resume from `verification_scheduled` after interruption.
- `tests/test_auto_update_loop.py` verifies that changing a registered verification task is rejected.
- `tests/test_auto_update_loop.py` verifies no-candidate terminal success without active-memory mutation.

These automated checks cover forced interruption and changed-task rejection paths that are awkward to reproduce safely against the live Codex backend without manufacturing non-representative runtime failures.

## Key Artifact Paths

Baseline bundle:

- `stage07-2-work-baseline/context.json`
- `stage07-2-work-baseline/result.md`
- `stage07-2-work-baseline/evidence.json`
- `stage07-2-work-baseline/outcome.json`
- `stage07-2-work-baseline/reflection.md`
- `stage07-2-work-baseline/policy_suggestions.md`
- `stage07-2-work-baseline/review_decisions.json`
- `stage07-2-work-baseline/memory_rules.md`

Verification bundle:

- `stage07-2-work-verification/context.json`
- `stage07-2-work-verification/result.md`
- `stage07-2-work-verification/evidence.json`
- `stage07-2-work-verification/outcome.json`
- `stage07-2-work-verification/comparison.json`
- `stage07-2-work-verification/comparison.md`

The committed summary JSON records SHA-256 digests for each listed artifact without committing the underlying runtime files.

## Validation

Local checks executed during Stage 07.2 work:

- `python3 -m unittest -q tests.test_auto_update_loop`
- `python3 -m unittest -q tests.test_diagnostics_health`
- real Codex baseline/review/verification acceptance using the committed Stage 07.2 fixtures
- real Codex no-candidate acceptance using the committed Stage 07.2 fixture
- `python3 tools/check_health.py --data-root <temp-data> --state-dir <temp-state> --model-backend codex`

GitHub CI for the release PR must be green before merge and tag creation. The repository keeps the local acceptance record and sanitized artifact manifest; CI status is tracked in the release PR itself.

## Known Limits

- Review operator identity verification is still not implemented.
- Acceptance scoring remains the current contract-based lexical check over required terms; it is not a full semantic grader.
- Runtime bundles remain local-only and are intentionally excluded from Git.

## Next Stage

`Review Operator Identity Verification`
