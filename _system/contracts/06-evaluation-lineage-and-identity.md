# Phase 5 Contract — Evaluation Lineage & Identity

**Version**: 0.1.0
**Status**: frozen
**Frozen**: 2026-07-10
**Source**: Phase 5 Design Draft v4.2.1 (Lineage Hardening Amendment)

---

## 1. Comparison Lineage (`LearningLoop.compare()`)

```
1. Validate compare() parameters, load both runs
2. REJECT if first.task_id != second.task_id
   (hard precondition, before all experiment-boundary checks)
3. UNCONDITIONALLY verify, for the same-task pair:
   - input identity: sorted (path, sha256) pairs of both evidence.json inputs are equal
   - workspace, project, context_limit are equal
   - memory_condition differs (one without_memory, one with_memory)
4. Write validated task_id into comparison.json
```

- `comparison.task_id` is the authoritative carrier of task identity for
  Phase 5. Downstream layers copy it and verify equality against their own
  inputs; no downstream layer may re-derive the fact or proceed when its
  inputs disagree with `comparison.task_id`.
- Input identity is **path-level**, not content-set-level: renamed inputs or
  collapsed duplicates are a different experiment.

## 2. Evaluation Adapter Integrity (`evaluate_experiment_bundle()`)

Pure in-memory adapter. For the identity, run-wiring, score, and threshold
conditions below, violations raise `EvaluationInputError`:

| # | Condition |
|---|-----------|
| 1 | `experiment.task_id` is a non-empty string |
| 2 | `experiment.task_id == comparison.task_id` |
| 3 | `comparison.first_run_id == experiment.without_memory_run_id` |
| 4 | `comparison.second_run_id == experiment.with_memory_run_id` |
| 5 | `without_memory_outcome.run_id == comparison.first_run_id` |
| 6 | `with_memory_outcome.run_id == comparison.second_run_id` |
| 7 | every score in {both outcome scores, comparison.first_score, comparison.second_score, comparison.score_delta} is present and a finite number (bool/NaN/Infinity rejected) |
| 8 | `comparison.first_score == without_memory_outcome.score` (tolerance 1e-9) |
| 9 | `comparison.second_score == with_memory_outcome.score` (tolerance 1e-9) |
| 10 | `comparison.score_delta == second_score - first_score` (tolerance 1e-9) |
| 11 | `thresholds` carries `utility_delta_min` (number), `verified_ratio_min` (number in [0,1]), `defined_before_run` (bool) |

Element-level shape of `memory_records` entries is outside this contract's
guarantee; only the container type is validated at the adapter boundary.

Pre-existing memory-direction gate is unchanged: `without` run must have empty
`used_memory_ids`, `with` run must have non-empty `used_memory_ids`.

**Explicitly NOT gates**:
- `comparison.passed` / `checks` do not gate evaluation.
- No coverage check between `memory_records` and `used_memory_ids`.

## 3. `utility_evaluation` v2 & Deterministic Identity

- Single producer: `build_utility_evaluation()` (evidence.py). The producer
  itself rejects missing/mismatched `experiment.task_id` / `comparison.task_id`
  — lineage holds at the producer boundary, not only behind the adapter.
- Output: `schema_version: 2`, `evaluation_id`, `experiment.task_id`.
- `evaluation_id = "eval_" + SHA-256(canonical JSON of identity payload)`,
  full digest, never truncated.
- Canonicalization: `sort_keys=True, separators=(",",":"), allow_nan=False`.

### Identity payload (exactly these 13 fields)

```json
{
  "schema_version": 2,
  "experiment": {
    "task_id": "",
    "without_memory_run_id": "",
    "with_memory_run_id": ""
  },
  "comparison": {
    "first_score": 0.0,
    "second_score": 0.0,
    "score_delta": 0.0
  },
  "evidence_composition": {
    "verified": 0,
    "unknown": 0,
    "contradicted": 0
  },
  "thresholds": {
    "utility_delta_min": 0.0,
    "verified_ratio_min": 0.0,
    "defined_before_run": true
  }
}
```

### Exclusions (never enter identity)

`utility.pack_utility_delta`, `evidence_sufficiency.*`, `validation_verdict`,
`memory_record_source`, `staleness_warning`, per-memory ids, any threshold key
outside the whitelist above.

Rationale: derived fields are fully determined by the identity facts; adapter
metadata is not evaluation semantics. Per-memory identity is audit provenance,
not evaluation-verdict semantics — identical aggregate counts yield identical
semantics and must share an ID.

### Drift guard

Any future threshold that affects verdict or sufficiency MUST be added to the
identity whitelist in the same change that introduces it. An unpromoted extra
threshold key never changes the ID.

## 4. Phase 5 Enrichment (copy-only snapshot)

`build_enriched_utility_evaluation()` (enrichment.py) is a pure function:
formal v2 evaluation → enriched dict with **exactly two fields**:

```json
{
  "source_evaluation_id": "eval_<sha256>",
  "source_evaluation_snapshot": {}
}
```

- `source_evaluation_snapshot` is a canonical object copy equal to the input.
  Copy-only: no recomputation, no dropping, no addition of Phase 4 facts.
- Rejects non-v2 input, missing `evaluation_id`, NaN/Infinity values.
- **No `source_evaluation_hash`**: not authorized by v4.2.1 §2.4. Adding it
  requires amending the controlling design first.
- No I/O, no file naming, no on-disk immutability enforcement at this layer.

## 5. Non-Goals (frozen)

- No `previous_evaluation_id`, evaluation registry, or separate identity module.
- No persistence of Phase 4 runtime internals; the adapter performs no I/O.
- No change to utility formula, evidence composition, validation verdict, or
  Memory lifecycle.
- No automated reflection, queue consumption, rule learning, or
  promotion/deletion.
- No trust / ranking / recommendation / auto-governance fields in any
  comparison, evaluation, or enrichment artifact (cross-cutting invariant).

## 6. Naming

Phase 5 Human Review Queue item identity uses `review_queue_item_id`.
Phase 3's `review_id` (conversation review session) is untouched.
