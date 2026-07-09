# Phase 4.0 — Memory Utility Success Signal

Date: 2026-07-08
Status: draft
Supersedes: none

---

## 1. Purpose

LAOS writes memory. LAOS reads memory. But LAOS cannot yet answer the first-order question:

> **Can LAOS prove that an Agent actually read, understood, and positively used a memory?**

Phase 4 exists to define and validate **Memory Utility**: the measurable signal that a retrieved memory made a demonstrable, positive contribution to a task outcome. Without this signal, memory is a write-only append log — recorded, never validated.

This document defines the Success Signal that gates all Phase 4 implementation work.

---

## 2. Evidence Baseline

Based on the `laos-v0.9` codebase read-only audit (2026-07-08):

### Existing (can reuse or extend)

| Mechanism | Location | Relevance |
|-----------|----------|-----------|
| **Context Pack** | `src/memory.py:context` command, `README.md` rules | Generates an Agent Context Pack. But it is **not** a verified context — no proof that the Agent actually read or followed it. |
| **Outcome schema** | `schemas/loop-run.schema.json`, `schemas/reflection-result.schema.json` | `outcome: pass` / `fail` per Learning Loop run. Mature, tested, can be extended for utility measurement. |
| **Memory used section** | `src/learning_loop/loop.py:280`, `docs/stage_07_learning_loop.md` | Learning Loop requires an exact `Memory used:` section in reflections. Existing format can be extended into a structured usage trace. |
| **Safety gate** | `src/safety/approval.py` (ApprovalGate), `src/safety/loop.py` (ToolLoopDetector) | Gate/approval pattern can be referenced for compliance-layer design, though compliance Layer is not built. |

### Missing (must define from scratch for Phase 4)

| Concept | Status |
|---------|--------|
| verified context | ❌ No mechanism to prove Agent read the context |
| verification receipt | ❌ No non-repudiable evidence of memory consumption |
| trust tier | ❌ No concept of graded memory trust |
| utility (success signal) | ❌ No definition of what "memory helped" means |
| privacy | ❌ No privacy protection module |
| redaction | ❌ No memory editing/removal mechanism |
| compliance | ❌ No compliance verification |
| usage trace | ❌ No structured pipeline for recording memory use events |

---

## 3. Definition: Memory Utility

**Memory Utility** is the measurable, attributable, positive contribution of a retrieved memory to a task outcome.

Key distinctions:

- Memory Utility is **NOT** "the Agent behaved differently after receiving memory" — behavioral difference is correlation, not causation.
- Memory Utility is **NOT** "the Agent cited the memory" — citation is necessary but insufficient.
- Memory Utility **IS**: given the same task, same model, same tools, a task that retrieves and properly applies a memory produces a measurably better outcome than one that does not.

In other words: **memory is useful if and only if its presence causally improves task results**.

---

## 4. Success Signal Layers

### Layer 1: Usage Signal

*Does the Agent receive and reference the memory?*

- Memory was retrieved into context (binary: yes/no)
- Agent output references the memory content (string match or semantic overlap)
- Usage is recorded as a structured event (what memory, when, in which task)
- **Minimum bar**: `memory_used: true` in task record

**Evidence type**: log entry, trace event.

### Layer 2: Compliance Signal

*Does the Agent use the memory correctly?*

- Agent does not fabricate memory content not present in the retrieved record
- Agent does not bypass memory (e.g., hardcode expected values, ignore retrieved content)
- Agent does not misuse memory (e.g., apply a memory from project A to project B when project isolation is required)
- Agent respects access boundaries (no privacy leaks, no cross-project contamination)

**Evidence type**: audit of input context vs output content vs retrieved memory.

**Existing reference**: Safety gate pattern (`src/safety/approval.py`) can be adapted for compliance checking at the compliance layer.

### Layer 3: Outcome Signal

*Does memory presence improve task results?*

- Same task executed with memory → outcome score
- Same task executed without memory (or with a no-op retrieval) → baseline outcome score
- **Utility = outcome_with_memory − outcome_without_memory** (delta)

This does NOT require a permanent A/B harness (see Non-goals). Outcome Signal can be established through:
- Dedicated benchmark tasks with known correct answers
- Repeated within-run measurement (e.g., Learning Loop's comparison mechanism in `src/learning_loop/loop.py:673`)
- Human review of matched pairs (memory vs no-memory)

---

## 5. Non-goals

The following are explicitly **out of scope** for Phase 4.0:

- ❌ **A/B harness implementation**: Outcome Signal benchmarking does not require a permanent A/B infrastructure. Benchmark tasks are sufficient.
- ❌ **Verified Context Pack**: The Context Pack remains an unverified context. Verified context is a future concern.
- ❌ **Trust tier system**: No trust metadata on memories. All memories are equal for Phase 4.
- ❌ **Privacy / redaction module**: No access control, content redaction, or memory deletion. These are future Phase concerns.
- ❌ **Sliver decomposition**: No Slivers are defined until the Success Signal is reviewed and approved.
- ❌ **Full repository refactoring**: No changes to existing code, tests, schemas, or modules.

---

## 6. Open Questions

1. **How to measure "better" outcomes?**
   - Learning Loop has `score` in outcome schema. Can this be extended, or does utility need a separate metric?
   - For open-ended tasks (e.g., writing), what objective scale applies?

2. **Which tasks serve as utility benchmarks?**
   - Existing integration tests? Dedicated benchmark suite?
   - Must be tasks that have a known correct answer (for Outcome Signal delta).

3. **Where should usage trace be recorded?**
   - New trace file per run? New field in `outcome.json`? New table in the state DB?
   - How does trace relate to `Memory used:` section in Learning Loop reflections?

4. **Does Verified Context Receipt come before Utility Validation?**
   - If an Agent cannot prove it read the context (Usage Signal), Outcome Signal is meaningless.
   - Should Verified Context be a prerequisite dependency for Phase 4, or can it be built in parallel?

5. **Should User Override / Redaction enter the Phase 4 Domain Model?**
   - If a user manually overrides or redacts a memory before it is retrieved, does that affect utility measurement?
   - Should the domain model account for user intervention, or is that a separate concern?

---

## 7. Decision

> **No Phase 4 Sliver work begins until the Memory Utility Success Signal (this document) is reviewed and approved.**

All Phase 4.0 effort prior to approval is limited to:
- Design documentation
- Read-only audits
- Signal definition
- Open question resolution

This gating ensures every Sliver in Phase 4 has a measurable success criterion from day one.
