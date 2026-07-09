# Phase 4.0 — Design Freeze

Status: Frozen (Phase 4.0 Baseline)

Version: 0.1

Date: 2026-07-08

---

# Purpose

This document freezes the architectural baseline for Phase 4.

From this point forward, all PRDs, Sliver planning, implementation, review and validation must conform to this document unless a later ADR explicitly supersedes it.

This document intentionally contains no implementation details.

---

# Scope

Phase 4 focuses on one problem only:

> Make memory produce measurable utility for downstream agents.

Phase 4 does NOT redesign the existing memory engine.

Phase 4 does NOT redesign Recovery.

Phase 4 does NOT introduce new infrastructure.

---

# Phase Goal

Build a Memory Runtime capable of demonstrating:

- memory is selected intentionally
- memory is injected intentionally
- agents consume memory
- memory changes observable behaviour
- memory improves measurable task outcomes

The objective is Memory Utility, not Memory Storage.

---

# Frozen First Principles

The following principles are frozen.

## Principle 1

Memory exists only if it produces utility.

Storage alone creates no value.

---

## Principle 2

Evidence is superior to declaration.

Every architectural claim must be supported by observable evidence.

---

## Principle 3

Memory Runtime is independent from Recovery.

Recovery remains an independent subsystem.

Only proven architectural discipline may be reused.

---

## Principle 4

Recovery is a Candidate Reference Pattern.

Reusable:

- review gates
- observable stages
- auditability
- explicit decisions

Not reusable:

- Recovery pipeline semantics
- execution semantics
- promotion semantics

---

## Principle 5

Agent behaviour is the execution layer.

Memory Runtime does not execute work.

Agents execute work.

Memory Runtime influences agents.

---

## Principle 6

Success is measured by outcomes.

Behaviour difference alone is insufficient.

Utility requires measurable improvement.

---

# Frozen Domain Model

Phase 4 contains four domains only.

## Memory Domain

Responsible for memory representation.

Questions answered:

- what memory exists
- where memory originates
- whether memory remains valid

---

## Context Domain

Responsible for runtime context construction.

Questions answered:

- what should be selected
- what should be injected
- why

---

## Agent Interaction Domain

Responsible for runtime consumption.

Questions answered:

- what the agent received
- what the agent used
- whether memory affected behaviour

---

## Evaluation Domain

Responsible for measuring utility.

Questions answered:

- did memory help
- by how much
- under what conditions

---

No additional domains may be introduced during Phase 4 without ADR approval.

---

# Frozen Capability Sequence

Capability implementation order is frozen.

1.
Memory Representation

↓

2.
Context Construction

↓

3.
Agent Interaction

↓

4.
Observation

↓

5.
Evaluation

↓

6.
Utility Validation

Capabilities may expand internally.

Their ordering may not change.

---

# Existing Capabilities

The following existing systems are reused.

- Outcome schema
- Context Pack generation
- Safety Gate
- Review Gate
- Memory Used reporting

They are treated as existing infrastructure.

Phase 4 extends them.

Phase 4 does not replace them.

---

# Deferred Capabilities

The following remain intentionally outside Phase 4.0.

- implementation
- optimisation
- retrieval ranking
- caching
- UI
- visualization
- benchmark framework
- runtime tuning

---

# Explicit Non-goals

Phase 4 will not:

- redesign Recovery
- redesign Learning Loop
- redesign Reflection
- redesign Memory Storage
- redesign Rule Governance
- redesign Safety

---

# Sliver Planning Rule

Slivers may only be created after:

- Design Freeze approved
- PRD completed

No implementation work begins before both conditions are satisfied.

---

# ADR Requirement

Any proposal that changes:

- First Principles
- Domain Model
- Capability order
- Runtime boundaries

requires a new ADR before implementation.

---

# Exit Criteria

Phase 4.0 is considered complete when:

- First Principles are frozen
- Domain Model is frozen
- Runtime Lifecycle is frozen
- Reference Architecture is frozen
- Capability Map is frozen
- Design Freeze is frozen

Only then may Phase 4.1 (PRD) begin.

---

# Phase Status

Current Status:

Phase 4.0 Design Frozen

Next Phase:

Phase 4.1 — PRD

Implementation Status:

Not Started
