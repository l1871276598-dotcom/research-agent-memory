---
title: Phase 4.0 Memory Runtime Reference Architecture
phase: Phase 4
status: Draft
type: Architecture Design
author: ChatGPT
date: 2026-07-08
---

# Phase 4.0 Memory Runtime Reference Architecture

## 1. Purpose

Define Phase 4 Memory Runtime 的参考架构。

本文档只定义：

- Architecture Boundary
- Runtime Layers
- Evidence Flow
- Responsibility

本文档明确不定义：

- Implementation
- Repository Structure
- Module Layout
- APIs
- Sliver
- Runtime Schema

---

## 2. First Principle

Memory Runtime 不是一个存储系统。

Memory Runtime 是一个 Evidence System。

核心问题不是：

> Memory Exists

而是：

> Memory Produces Utility

因此 Architecture 必须围绕：

```
Evidence
instead of
Declaration
```

---

## 3. Architectural Principles

### Principle 1

**Evidence First**

### Principle 2

**Memory Utility over Memory Storage**

### Principle 3

**Agent Independence**

Memory Runtime 不控制 Agent 推理。

只提供：

- Memory
- Context
- Evidence

### Principle 4

**Governance before Automation**

所有高风险行为必须能够审计。

### Principle 5

**Utility over Activity**

证明：

> Memory 是否提升任务。

不是：

> Memory 是否存在。

---

## 4. System Boundary

```
Memory Runtime

    │

Context Runtime

    │

──────── Boundary ────────

    │

Agent Runtime

    │

Task Execution
```

**说明：**

Memory Runtime 到 Context Pack 为止。

Agent 如何推理：

- 属于 **Agent Runtime**
- 不属于 **Memory Runtime**

---

## 5. Runtime Layers

### Layer 1 — Memory Foundation

**职责：**

- Memory
- Metadata
- Trust
- Freshness
- Lifecycle

### Layer 2 — Context Runtime

**职责：**

- Selection
- Ranking
- Packaging
- Verification
- Receipt

### Layer 3 — Agent Interaction

**职责：**

- Injection
- Usage
- Reference
- Compliance

### Layer 4 — Evaluation

**职责：**

- Outcome
- Utility
- Comparison
- Signal

### Layer 5 — Governance

**职责：**

- Review
- Approval
- Audit
- Policy
- Safety

---

## 6. Data Flow

```
Memory

↓

Selection

↓

Context Pack

↓

Agent

↓

Outcome

↓

Evaluation
```

**说明：** 这是数据流。

---

## 7. Evidence Flow

```
Memory

↓

Verification Receipt

↓

Usage Trace

↓

Outcome

↓

Utility Evidence
```

**强调：**

**Evidence Flow** 不等于 **Data Flow**。

---

## 8. Trust Boundary

```
Memory Runtime

=====================

Agent Runtime
```

**说明：**

Trust 在 Runtime 边界停止。

Agent 可以：

- 使用
- 忽略
- 误用

Memory。

因此必须：

- Observation
- Evaluation

而不是假设 Agent 正确使用。

---

## 9. Relationship With Existing LAOS

| Existing | Future |
|----------|--------|
| Context Pack | Verification Receipt |
| Learning Loop Outcome | Utility Signal |
| Policy Review | Usage Trace |
| Safety Gate | Agent Compliance |
| Memory Used | Trust Tier |
| | Privacy |
| | Redaction |

---

## 10. Relationship With Recovery Reference Pattern

**强调：**

Recovery 不是模板。

Recovery 是：

> Candidate Reference Pattern

**仅继承：**

- Evidence
- Governance
- Review Gate
- Audit

**不继承：**

- Execution Semantics
- Runtime Structure
- Module Layout

避免出现：

> Recovery → Phase4 一一映射

---

## 11. Non Goals

本文档不：

- 修改代码
- 修改模块
- 修改 Runtime
- 修改 Learning Loop
- 修改 Review
- 修改 Memory Storage

---

## 12. Completion Criteria

```
Phase 4.0

✅ Memory Utility Success Signal

✅ Runtime Domain Model

✅ Runtime Lifecycle

✅ Reference Architecture

⬜ Capability Map

⬜ Phase 4 Design Freeze

⬜ PRD

⬜ Sliver Planning
```
