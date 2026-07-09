---
title: Phase 4.0 Memory Runtime Capability Map
phase: Phase 4
status: Draft
type: Architecture Design
author: ChatGPT
date: 2026-07-08
---

# Phase 4.0 — Memory Runtime Capability Map

## Purpose

This document defines the conceptual capabilities of the Phase 4 Memory Runtime.

It answers:

- What capabilities must exist?
- What responsibility does each capability own?
- Which capabilities already exist?
- Which capabilities are future work?

This document intentionally excludes:

- implementation
- module layout
- APIs
- repository structure
- Sliver planning

---

# First Principle

Capabilities describe **what the runtime must be able to do**.

They do **not** describe:

- how it is implemented
- which module owns it
- which class performs it

A capability is a responsibility.

---

# Capability Domains

Phase 4 consists of four domains.

```
Memory

↓

Context

↓

Agent Interaction

↓

Evaluation
```

Each domain owns a group of capabilities.

---

# Domain A — Memory

Responsible for maintaining reusable knowledge.

## Capability A1 — Memory Registration

Purpose

Register newly created memory.

Input

- New memory

Output

- Candidate memory

Current Status

🟡 Partial

Existing Support

- Conversation archive
- Learning artifacts

---

## Capability A2 — Memory Provenance

Purpose

Track origin of memory.

Input

- Memory

Output

- Provenance metadata

Current Status

🟡 Partial

Existing Support

- Source references

Future

- Full provenance chain

---

## Capability A3 — Memory Trust

Purpose

Represent confidence in memory.

Input

- Memory

Output

- Trust metadata

Current Status

🔴 Future

---

## Capability A4 — Memory Freshness

Purpose

Represent temporal validity.

Input

- Memory

Output

- Freshness metadata

Current Status

🟡 Partial

Existing Support

- Freshness calculation

Future

- Runtime freshness policy

---

## Capability A5 — Memory Lifecycle

Purpose

Manage semantic state.

Current Status

🟡 Partial

Future States

- Candidate
- Reviewed
- Approved
- Active
- Revoked
- Archived

---

# Domain B — Context

Responsible for transforming Memory into runtime context.

## Capability B1 — Context Selection

Purpose

Determine relevant memories.

Current Status

🟡 Partial

Existing Support

- Retrieval
- Ranking

---

## Capability B2 — Context Packaging

Purpose

Produce runtime context.

Current Status

🟢 Existing

Existing Support

- Context Pack

---

## Capability B3 — Verification Receipt

Purpose

Provide evidence explaining Context Pack generation.

Current Status

🔴 Future

---

## Capability B4 — Context Verification

Purpose

Verify context integrity.

Current Status

🔴 Future

---

# Domain C — Agent Interaction

Responsible for runtime interaction between memory and agent.

## Capability C1 — Context Injection

Purpose

Deliver Context Pack.

Current Status

🟢 Existing

---

## Capability C2 — Usage Observation

Purpose

Observe memory usage.

Current Status

🟡 Partial

Existing Support

- Memory Used section

Future

- Usage Trace

---

## Capability C3 — Agent Compliance

Purpose

Verify correct memory usage.

Current Status

🔴 Future

Checks

- Hallucinated memory
- Ignored memory
- Revoked memory
- Trust violations

---

# Domain D — Evaluation

Responsible for proving Memory Utility.

## Capability D1 — Outcome Collection

Purpose

Collect task results.

Current Status

🟢 Existing

Existing Support

- Outcome
- Pass / Fail

---

## Capability D2 — Utility Evaluation

Purpose

Determine positive contribution.

Current Status

🔴 Future

---

## Capability D3 — Success Signal

Purpose

Measure runtime effectiveness.

Current Status

🔴 Future

---

## Capability D4 — Continuous Improvement

Purpose

Allow runtime evolution.

Current Status

🟡 Partial

Existing Support

- Learning Loop

Future

- Utility-driven improvement

---

# Capability Dependency

```
Memory Registration
        ↓
Memory Provenance
        ↓
Memory Trust
        ↓
Memory Selection
        ↓
Context Packaging
        ↓
Verification Receipt
        ↓
Context Injection
        ↓
Usage Observation
        ↓
Agent Compliance
        ↓
Outcome Collection
        ↓
Utility Evaluation
        ↓
Success Signal
```

Capabilities build upon each other.

---

# Existing Capability Summary

Existing

✅ Context Pack

✅ Retrieval

✅ Outcome

✅ Learning Loop

✅ Safety Gate

✅ Review Workflow

✅ Memory Used

Partial

🟡 Provenance

🟡 Freshness

🟡 Usage Observation

🟡 Memory Lifecycle

🟡 Continuous Improvement

Future

🔴 Verification Receipt

🔴 Trust Tier

🔴 Context Verification

🔴 Agent Compliance

🔴 Utility Evaluation

🔴 Success Signal

---

# Capability Boundaries

Memory Runtime owns:

- Memory
- Context
- Evaluation

Memory Runtime does not own:

- Agent reasoning
- LLM planning
- Tool execution
- User interaction

---

# Relationship with Existing LAOS

Phase 4 extends existing capabilities.

It does not replace:

- Learning Loop
- Review
- Safety
- Context Pack
- Memory Storage

Future capabilities should reuse existing mechanisms whenever possible.

---

# Non Goals

This document does not define:

- implementation
- schemas
- APIs
- repositories
- runtime modules
- Slivers

---

# Completion Criteria

Phase 4.0 Design Documents

✅ Memory Utility Success Signal

✅ Runtime Domain Model

✅ Runtime Lifecycle

✅ Reference Architecture

✅ Capability Map

⬜ Design Freeze

⬜ PRD

⬜ Sliver Planning
