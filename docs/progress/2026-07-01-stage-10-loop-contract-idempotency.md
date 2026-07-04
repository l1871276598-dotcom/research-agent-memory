# Stage 10 — Loop Contract and Idempotency

Date: 2026-07-01
Decision: PASS

## Scope

Stage 10 stabilizes the existing lightweight Loop Engineering data contract and makes repeated `finalize` calls idempotent. It does not add autonomous reflection, automatic policy application, automatic candidate acceptance, shell execution, MCP, UI, or a Meta Planner.

## Implemented contract

New Loop runs keep the public `schema_version: 1` for v0.8 API compatibility and add an explicit contract identity:

- `contract_version: 2`
- `contract_id: laos.loop-run.v2`
- deterministic `run_id`
- full `idempotency_key`
- task and result SHA-256 values
- reflection, policy, and candidate status fields
- policy suggestion IDs and approved policy IDs
- generated candidate IDs

The machine-readable contract is defined in `schemas/loop-run.schema.json`. Runtime validation also enforces cross-field invariants, including candidate status/link consistency and policy/run status consistency.

## Idempotency behavior

The idempotency key is the SHA-256 digest of canonical JSON containing:

- task
- result
- outcome
- error evidence
- supplied reflection
- normalized policy suggestions

Policy suggestions are whitespace-normalized, blank entries are removed, and exact duplicates are collapsed while preserving order.

Repeating the same finalized Loop input:

- returns the same `run_id` and path
- reports `loop_run.reused: true`
- does not create another run directory
- does not create another candidate
- does not append another distillation journal event

A blank result remains a valid idempotent run with `candidate_status: not_generated` and no candidate linkage.

## Compatibility

Legacy v1 `run.json` files without `contract_id` and `contract_version` remain readable and approvable. New v2 contract files are validated strictly and inconsistent or tampered status/link combinations are rejected as `invalid_loop_run`.

## Rollback hardening

The pre-existing distillation journal snapshot is now read inside the protected finalize transaction boundary. If the snapshot cannot be read, candidate distillation does not begin and the already-published Loop run directory is removed. Existing candidate, journal, and index rollback behavior remains intact.

## Safety boundaries retained

- Loop files remain in the local state directory, not the iCloud data root.
- The authoritative data root and local runtime state remain separate.
- Generated memories remain `candidate` only.
- Review Gate remains the only path to `active` memory.
- Policy approval does not automatically change prompts, code, Agent behavior, or active memory.
- No automatic retry, shell execution, or unattended apply path was added.

## Verification

- 317 existing tests continued to pass after compatibility correction.
- 8 Stage 10 contract, idempotency, and schema tests were added.
- Final full regression: 325 tests passed, 0 failures.
- Final recorded duration: 47.105 seconds.

The new tests cover:

1. repeated finalize reuse without repeated side effects
2. normalized policy suggestion idempotency
3. legacy v1 approval compatibility
4. v2 contract inconsistency rejection
5. journal snapshot failure cleanup
6. blank-result idempotency
7. Loop JSON Schema parsing and contract identity
8. required runtime contract field alignment

## Next stage

Stage 11 is the deterministic Reflection Agent. It should consume the stable v2 Loop contract, preserve evidence boundaries, use `unknown` when evidence is missing, and produce suggestions only. It must not write active memory or approved rules directly.
