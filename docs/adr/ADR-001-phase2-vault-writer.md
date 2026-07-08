# ADR-001: Phase 2 Vault Writer — Design Decisions

Date: 2026-07-08
Status: implemented (main @ 84c03dc)
Supersedes: none

## Context

Phase 2 introduces a write pipeline for vault notes: candidates are created, reviewed,
planned, and finally promoted into the vault. The pipeline is designed to prevent
partial writes, conflicting overwrites, and silent data loss.

This document records the decisions that are not evident from reading the code — the
architectural rationale that future readers would otherwise reverse-engineer or miss.

---

## Decision 1: Promotion Plans as explicit state machine, not direct promotion

**Decision:** `promote()` reads a `promotion_plans` row and validates its `state`
before acting. It does not accept a candidate ID directly.

**Rationale:** Separating *what* to promote (the plan) from *how* to promote (the
execution) creates an audit checkpoint between approval and write. A plan captures
the exact target path and the expected hash at planning time, so any change to the
candidate between planning and promotion is detected by `check_promotion_conflicts`.
Without this indirection, concurrent changes to the candidate after approval could
go undetected, and there would be no documented intent of *where* to write.

**Consequence:** A plan must be explicitly created (`plan_promotion()`) before
promotion can happen. This adds one extra step to the pipe but makes the promotion
intent inspectable, auditable, and replayable.

---

## Decision 2: Two separate hashes — `expected_candidate_hash` vs `reviewed_hash`

**Decision:** `promotion_plans.expected_candidate_hash` is a separate column from
`candidates.reviewed_hash`. They can differ.

**Rationale:** Each hash records a different invariant:

- `reviewed_hash` — the hash of the content that was actually reviewed by a human
  (set at review time by `review_candidate()`). It never changes once set.
- `expected_candidate_hash` — the hash of the content at *plan creation time*,
  set by `plan_promotion()`. If someone edits the candidate file after the plan
  is created but before promotion, this hash diverges from the live file, and
  `check_promotion_conflicts` detects the mismatch.

Using only `reviewed_hash` would miss edits made *after* review but *before*
promotion — a window that can be arbitrarily long in an asynchronous pipeline.
Using only `expected_candidate_hash` would lose the fact that review happened
on a specific version.

**Consequence:** On conflict, the reviewer can compare both hashes to distinguish
"approved content was edited before promotion" from "approved content was edited
after planning" — though in practice both mean the same recovery: re-review.

---

## Decision 3: Fail-closed on any conflict

**Decision:** `promote()` raises `ValueError` on any conflict (hash mismatch, target
already exists, symlink escape). It does not overwrite, ignore, or auto-resolve.

**Rationale:** Three failure modes share the same response because each would
produce a vault state that is either wrong or unrecoverable:

- **Hash mismatch** — the candidate changed after planning. Promoting the old
  hash would promote reviewed-but-outdated content. Promoting the new hash would
  promote unreviewed content.
- **Target exists** — something already occupies the target path (manual note,
  earlier promotion). Overwriting would destroy existing content without recovery.
- **Symlink escape** — the resolved target path lies outside the vault root.
  Writing there would bypass vault boundaries entirely.

In all three cases it is safer to abort than to proceed. The caller (CLI, API)
is expected to surface the error; the candidate enters `conflicted` state so it
won't be silently retried.

**Consequence:** Every promotion failure requires human intervention to resolve
the underlying cause. There is no automatic retry.

---

## Decision 4: `promotion.orphan_source` is an audit event, not a transaction failure

**Decision:** If `source_path.unlink()` fails after a successful promotion (DB
updated, file written to target), the error is logged as
`promotion.orphan_source` but the promotion itself is considered successful.

**Rationale:** The `laos-generated/` candidate file is a cache, not the
authoritative copy. After promotion, the true content lives at the target path.
If the cache cannot be cleaned up, the vault is still consistent — the promoted
note is readable, audited, and active. Making this a transaction failure would
create a worse outcome: the target file and DB state would need to be rolled
back, even though the write itself succeeded.

The orphan record exists so an offline cleanup sweep can later remove stale
cache files, but it never blocks the promotion.

**Consequence:** A vault may accumulate orphan candidate files in
`0-inbox/laos-generated/` after failed unlinks. These are harmless but may
require occasional cleanup.

---

## Decision 5: `conflicted` state does not auto-recover

**Decision:** `check_promotion_conflicts` sets `candidate_state` to `conflicted`
but never resets it back to `approved`. No automatic recovery path exists.

**Rationale:** A `conflicted` candidate represents a breach of the invariant
"the file at plan time matches the file now." The resolution is not automatic
because the cause is not automatic — it was a human editing the file, a
concurrent process modifying it, or a filesystem-level change. Auto-recovering
would silently accept whichever version happened to exist at retry time, which
defeats the purpose of hash verification.

The correct recovery is human review of the current file, followed by either
re-creating the plan (if the file is acceptable) or discarding the candidate.
The `conflicted` state exists to make this visible and to prevent accidental
re-promotion via the normal pipe.

**Consequence:** Conflicted candidates remain in the database until a human
explicitly resolves them. A future tool (`resolve_conflict`) could automate
the re-review step, but the decision to re-approve is always human.

---

## Summary of invariants

| Invariant | Enforced by | Failure mode |
|---|---|---|
| Content promoted == content reviewed | `expected_candidate_hash` vs `promotion_plans` | `conflicted` + human re-review |
| No overwrite of existing notes | `target_path` existence check in `check_promotion_conflicts` | `ValueError`, no write |
| No symlink escape out of vault | `_validate_relative_path` + `resolve()` chain | `ValueError`, no write |
| Single promotion per plan | `plan.state` transition `active → completed` | `ValueError`, no re-execution |
| DB + file system consistency | `promote()` transaction wraps DB writes | Rollback: target file deleted on DB failure |
