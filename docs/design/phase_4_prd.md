# LAOS Phase 4 PRD

## Memory Utility Validation Phase

**Status:** Approved PRD Draft

---

# 1. Product Definition

## Problem

当前 Agent Memory 系统面临的问题不是：

Memory 是否可以存储更多信息。

而是：

> Memory 是否真正改善 Agent 的任务表现，并且这种改善是否可以被可信验证。

---

传统 Memory 系统容易陷入：

- Memory 越多越好；
- 使用越频繁越有价值；
- 被引用代表有效；

这些未经验证的假设。

---

Phase 4 的目标：

建立一个可验证的 Memory Utility 评价框架。

---

# 2. Product Goal

Phase 4 需要回答：

## Q1

Memory 是否进入 Agent 行为？

---

## Q2

Memory 是否改善任务结果？

---

## Q3

这种改善是否来自 Memory，而不是其他因素？

---

## Q4

如果 Memory 无价值，是否可以明确停止或收缩方向？

---

最终产出：

不是新的 Memory Runtime。

而是：

> 一个证明 Memory 是否值得继续投入的验证基础。

---

# 3. Scope

Phase 4 包含：

## S0 Memory Utility Foundation

定义：

- Memory Utility；
- Correctness；
- Baseline；
- Success Signal。

---

## S1 Memory Evidence Model

建立：

Memory 使用证据：

- selected；
- delivered；
- available；
- referenced。

Memory 可信证据：

- provenance；
- source；
- freshness；
- validation status；
- contradiction status。

---

## S2 Context Experiment Boundary

定义：

公平比较条件：

- Memory Condition；
- Baseline Condition；
- Isolation Boundary。

保证：

唯一实验变量：

Memory 是否存在。

---

## S3 Correctness-aware Utility Evaluation

评价：

Memory 是否改善任务结果。

核心：

Utility:

Outcome(with Memory)

-

Outcome(without Memory)

评价必须结合：

Memory Correctness Evidence。

---

## S4 External Validation

建立外部验证：

包括：

- Human review；
- External evaluator；
- Benchmark task。

防止系统自我证明。

---

# 4. Non-goals

Phase 4 不包含：

## Memory 生命周期治理

不做：

- 自动升级；
- 自动淘汰；
- 自动修复；
- Trust 自动增长。

---

## Runtime 重构

不做：

- Agent 架构修改；
- Context Runtime 重建；
- Memory Provider 重写。

---

## 自动治理

不做：

- 自动规则生成；
- 自动权限变化；
- 自动决策。

---

# 5. User Stories

## Story 1

作为 LAOS 开发者：

我需要知道 Memory 是否真的帮助 Agent。

验收：

能够比较：

With Memory

vs

Without Memory。


---

## Story 2

作为系统维护者：

我需要知道 Memory 的价值是否可信。

验收：

评价必须包含：

- Correctness Evidence；
- External Validation。


---

## Story 3

作为项目决策者：

我需要知道如果 Memory 没有价值怎么办。

验收：

系统能够输出：

- Positive Evidence；
- Negative Evidence；
- Insufficient Evidence。


---

# 6. Functional Requirements

## FR-1 Utility Definition

系统必须定义：

Memory Utility。

---

## FR-2 Evidence Collection

系统必须能够记录：

Memory 使用和可信信息。

---

## FR-3 Controlled Comparison

系统必须支持：

受控 Memory / No Memory 对比。

---

## FR-4 Utility Evaluation

系统必须能够生成：

Utility Evaluation Report。

---

## FR-5 External Validation

系统必须支持：

外部验证机制。

---

## FR-6 Outcome Classification

系统必须支持：

三种验证结果：

### Outcome A

Positive Evidence

---

### Outcome B

Negative Evidence

---

### Outcome C

Insufficient Evidence

---

# 7. Acceptance Criteria

Phase 4 PRD 完成标准：

## Criterion 1

能够证明：

Memory 是否进入 Agent 行为。

---

## Criterion 2

能够测量：

Memory 对任务结果影响。

---

## Criterion 3

能够区分：

Memory 使用价值

与

Memory 内容可信度。

---

## Criterion 4

能够避免：

自证循环。

---

## Criterion 5

能够接受：

Memory 无价值这一结果。

---

# 8. Validation Rules

必须遵守：

## Evidence over Declaration

---

## Utility ≠ Correctness

---

## Human Authority

系统只提供证据。

最终治理由人工决定。

---

## No Automatic Promotion

Utility 不改变 Memory 权限。

---

# 9. Dependencies

Phase 4 依赖：

已有：

- Context Pack 基础；
- Learning Loop outcome 机制；
- Safety Gate 模型。

需要确认：

这些作为输入能力。

不重新设计。

---

# 10. Risks

## Risk 1

Utility 测量偏差。

缓解：

External Validation。


---

## Risk 2

错误 Memory 产生假收益。

缓解：

Correctness-aware Evaluation。


---

## Risk 3

任务选择偏差。

缓解：

Task Dataset Independence Principle。


---

# 11. Next Phase Boundary

Phase 4 PRD 完成后：

进入：

Phase 4 Sliver Implementation Planning。


之后：

TDD

↓

Implementation


禁止：

在 PRD 阶段重新扩大 Phase 4 范围。
