# Stage 15 — v0.9 Architecture and Local Security Audit

Date: 2026-07-04
Decision: PASS after remediation
Scope: Stage 10 through Stage 14, including Loop run contracts, Reflection, Policy Learning, Low-risk Candidate Generation, the lightweight Coordinator, local state paths, artifact consistency, partition isolation, and Review Gate ownership.

## Existing uncommitted-file decision

The Stage 10–13 uncommitted files were inspected before the audit.

Retained for commit:

- Stage 10–13 progress reports
- Loop, Reflection, Policy, and Candidate JSON schemas
- Reflection Agent
- Policy Agent
- Low-risk Candidate Agent
- v0.9 Registry configuration
- Stage 10–13 unit, contract, schema, recovery, and CLI tests

These files form one coherent v0.9 Loop Learning implementation chain and are required by the runtime and validation suite.

Cleaned:

- `tests/.bridge_probe`

The removed file contained only the text `probe`. It was a Developer Bridge write-permission probe and had no product, test, documentation, or audit value. No other Stage 10–13 file was deleted.

## Audit method

The audit used independent source inspection plus adversarial regression tests. It reviewed:

- exact Registry-to-Agent matching
- Loop run path containment and symlink rejection
- run contract field and state consistency
- deterministic artifact reconstruction
- policy identity, duplicate, and conflict classification
- evidence fingerprint independence
- pending-intent recovery
- workspace and project partition isolation
- candidate-only Memory Core writes
- Review Gate authority enforcement
- CLI routing and unsupported-input rejection
- backward compatibility for legacy v1 and Stage 10 v2 runs

All discovered Major findings were reproduced with failing tests before repair.

## Findings and remediation

### Major 1 — Coordinator session candidate partition leak

Finding:

`loop.coordinate` passed `workspace` to the principle candidate stage but the session candidate created during `finalize` remained hard-coded to `personal/personal`. A work task result could therefore be written into the personal partition.

Remediation:

- `finalize_memory()` now accepts optional `workspace` and `project` provenance.
- Session candidate workspace, confidentiality, and scope are derived from those explicit inputs.
- Coordinator forwards the same partition to finalization and principle candidate generation.
- Workspace/project participate in run identity only when explicitly supplied, preserving old Stage 10 v2 identities when absent.

Verification:

The same task submitted to work and personal partitions creates different Loop run IDs and session candidates in the correct partitions.

### Major 2 — Pending candidate intent evidence bypass

Finding:

A pre-existing `pending_creation` generation artifact was structurally checked but its recorded evidence was not revalidated. A locally forged artifact could claim three evidence records and bypass the minimum threshold.

Remediation:

- Every reused generation intent is re-evaluated against current validated Loop evidence.
- Recorded run ID and task/result fingerprint pairs must still exist.
- The three-independent-evidence threshold must still be met.
- Duplicate or conflicted policy evidence invalidates reuse.

Verification:

A forged pending intent with fabricated run and fingerprint values fails closed and creates no principle candidate.

### Major 3 — Coordinated Reflection and Policy artifact tampering

Finding:

Stage 13 previously verified that the Policy artifact referenced the current Reflection artifact bytes, but did not reconstruct Reflection from `reflection.md` and `run.json`. An attacker who changed both Reflection content and the Policy hash consistently could make the chain appear internally consistent.

Remediation:

- Stage 13 reconstructs the exact expected Reflection output from the immutable run evidence and source reflection document.
- Reflection and Policy artifacts require exact top-level and candidate shapes.
- Policy IDs, normalized text, directive effect, subject, duplicate state, and conflict state are recomputed and checked.

Verification:

Coordinated Reflection and Policy tampering is rejected before evidence can contribute to candidate generation.

### Major 4 — Cross-workspace and cross-project evidence aggregation

Finding:

Loop runs did not record workspace/project provenance. Low-risk Candidate Generation therefore aggregated matching policies globally. Three work runs could be used to create a personal principle candidate, and evidence could be mixed between projects.

Remediation:

- New v2 runs record `workspace` and `project` together when partition provenance is supplied.
- Runtime and JSON Schema reject incomplete partition pairs and unknown fields.
- Existing Stage 10 v2 runs without partition fields remain valid and are interpreted as personal/global.
- Candidate evidence is aggregated only when run workspace and project exactly match the requested destination partition.

Verification:

- Work evidence cannot create a personal principle candidate.
- Work evidence can create only a work candidate.
- Evidence for `project-a`, `project-b`, and global scope is counted independently.

### Major 5 — Standalone Policy Agent accepted forged Reflection structure

Finding:

The Policy Agent checked the Reflection source hash and basic fields but did not require the structured Reflection output to equal the deterministic reconstruction from `reflection.md` and `run.json`. The downstream Candidate Agent would reject it, but standalone policy review could still display forged content.

Remediation:

- Policy Agent now requires the exact Reflection artifact shape.
- It reconstructs and compares the full Reflection output before generating policy candidates.

Verification:

Structurally coordinated Reflection tampering is rejected and produces neither `policy_candidates.json` nor `policy_review.md`.

## Path and artifact safety conclusions

### Loop state paths

- The configured state directory is validated against the data root.
- `loop_engineering`, `runs`, and generated-candidate directories reject symlinks and non-directories.
- Run IDs are fixed lowercase hexadecimal identifiers.
- Run directories must resolve as direct children of the validated runs directory.
- Required artifacts reject symlinks and non-regular files.
- Coordinator-returned artifact paths are resolved and checked inside the state directory.

### Artifact consistency

The validated chain is now:

```text
run.json + reflection.md
→ deterministic reflection_result.json
→ deterministic policy_candidates.json
→ partition-scoped independent evidence
→ candidate-only principle record
```

Each downstream stage reconstructs or revalidates its upstream evidence. A self-consistent modification of downstream hashes is insufficient to bypass source validation.

### Review Gate boundary

The audit confirmed:

- Memory Core exposes candidate creation, reads, and search only.
- Reflection, Policy, Candidate, and Coordinator Agents cannot write active memory.
- Principle generation always writes `status: candidate`.
- `memory_rules.md` is not written by the Coordinator or Candidate Agent.
- Active promotion requires Review Gate authority.
- The internal acceptance implementation rejects calls without the private Review Gate authority object.
- Legacy CLI acceptance routes through Review Agent and Review Gate rather than calling the implementation directly.
- Workspace and project references are rechecked during review.

No activation bypass was found.

## Architecture conclusions

The Stage 10–14 design remains lightweight:

- no background watcher
- no autonomous task execution
- no automatic retries
- no automatic policy approval
- no semantic conflict auto-resolution
- no candidate auto-activation
- no large coordinator state machine

The Coordinator is an ordered adapter over existing durable and idempotent stages. Business rules remain owned by the specialized components.

## Remaining accepted limitations

These are explicit non-goals rather than audit failures:

- reviewer/operator identity authentication is not implemented
- owner-agent authentication is not implemented
- semantic conflict resolution remains manual
- the Coordinator does not monitor or execute tasks continuously
- the built-in literature system remains out of scope

These limitations do not weaken the current candidate-only and Review Gate security boundary.

## Final verification

The final verification suite executed:

```text
364 tests passed
compileall passed
git diff --check passed
git diff --cached --check passed
```

Security-focused regressions include:

1. work session candidates cannot enter personal memory
2. work evidence cannot create personal principles
3. project evidence cannot cross project or global boundaries
4. forged pending intents cannot bypass evidence thresholds
5. coordinated Reflection/Policy tampering fails closed
6. standalone Policy Agent reconstructs Reflection before use
7. incomplete or unknown partition fields invalidate a partitioned v2 run
8. old v2 runs without partition fields remain compatible
9. Review Gate remains the only active-memory promotion path

## Audit result

- Critical: 0
- Major: 5 found and fixed
- Minor: 0
- Suggestion: 0

Stage 15 passes after remediation. The Stage 10–15 implementation set is suitable for one local v0.9 development commit. Stage 16 release review, remote push, PR creation, merge, and release remain separate gated steps.
