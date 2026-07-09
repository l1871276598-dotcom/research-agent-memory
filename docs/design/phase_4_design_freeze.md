# LAOS Phase 4 Design Freeze

## Memory Utility Validation Phase

**Status:** Design Frozen

**Purpose:**
Establish whether LAOS Memory produces measurable, trustworthy utility for downstream agents.

---

# 1. Phase 4 Final Objective

Phase 4 的目标不是：

> 构建更强的 Memory 系统。

也不是：

> 证明 Memory 一定有价值。

Phase 4 的真正目标：

> 建立一个可证伪、可审计的方法，判断 Memory 是否在受控条件下产生可信 Utility。

---

Memory 可能：

- 被忽略；
- 没有实际帮助；
- 包含错误信息；
- 增加上下文噪声；
- 降低 Agent 表现。

因此：

Memory 的存在价值必须通过证据验证，而不是默认成立。

---

# 2. Frozen Core Principles

## 2.1 Evidence over Declaration

所有 Memory 价值判断必须基于：

- 可观察证据；
- 可验证结果；
- 可追踪记录。

禁止：

仅根据：

- 使用次数；
- 主观评价；
- Memory 存在本身；

推断价值。

---

## 2.2 Utility ≠ Correctness

Phase 4 永久区分：

### Utility

Memory 是否改善任务结果。


### Correctness

Memory 内容是否可靠。


关系：

Correctness 决定：

Memory 是否值得被信任。


Utility 决定：

Memory 是否产生实际价值。


禁止：

Memory 被使用 + 任务成功

直接推导：

Memory 高价值。

---

## 2.3 Human Authority

Phase 4 只提供：

- 证据；
- 评价；
- 验证结果。

不自动决定：

- Memory 晋升；
- Trust 调整；
- 生命周期变化。

最终治理权属于人工。

---

## 2.4 No Automatic Trust Promotion

禁止：

使用次数增加

↓

自动提高 Memory 信任等级。


禁止：

Utility 结果直接改变 Memory 权限。

---

## 2.5 No Recovery Pipeline Migration

Phase 4 继承 Recovery：

- Evidence；
- Auditability；
- Explicit Boundary；
- Review Gate。

不继承：

- Promotion；
- State Machine 晋升；
- 自动恢复语义。

---

# 3. Frozen Phase 4 Architecture

Phase 4 固定包含五个 Sliver。

---

# S0 — Memory Utility Foundation

目标：

定义 Memory Utility。

冻结内容：

- Utility Definition；
- Correctness Definition；
- Baseline Definition；
- Success Signal。


输出：

Memory Utility Model

---

# S1 — Memory Evidence Model

目标：

建立 Memory 证据模型。

包含：

## Usage Evidence

记录：

- selected；
- delivered；
- available；
- referenced。


## Correctness Evidence

记录：

- provenance；
- source；
- freshness；
- validation status；
- contradiction status。


限制：

Phase 4 只记录已知状态。

不负责：

- 自动冲突检测；
- 自动修复；
- 自动淘汰。

输出：

Memory Evidence Record

---

# S2 — Context Experiment Boundary

目标：

建立公平比较条件。


冻结要求：

定义：

- Memory Condition；
- Baseline Condition；
- Isolation Boundary。


保证：

实验唯一变量：

Memory 是否存在。


同时包含：

## Task Dataset Independence Principle

任务集合必须记录：

- 来源；
- 选择规则；
- 覆盖范围；
- 是否由 Memory 建设方单独选择。


若任务明显偏向 Memory 优势：

不能作为强 Positive Evidence。

---

输出：

Context Experiment Boundary Specification

---

# S3 — Correctness-aware Utility Evaluation

目标：

判断：

Memory 是否改善任务结果。


核心：

Utility = Outcome(with Memory) - Outcome(without Memory)


冻结要求：

评价必须结合 Correctness Evidence。


报告必须：

按 Memory 状态区分：

|状态|处理|
|-|-|
|verified|可作为价值证据|
|unknown|观察结果|
|contradicted|不作为价值证明|


同时披露：

- verified 比例；
- unknown 比例；
- contradicted 比例。


输出：

Utility Evaluation Report

---

# S4 — External Validation

目标：

防止系统自我证明。


验证来源：

- Human review；
- External evaluator；
- Benchmark task。


要求：

评价标准不能完全由 Memory 系统自身定义。


输出：

Validation Evidence Package

---

# 4. Frozen Validation Outcome Model

Phase 4 允许三种结果。

---

## Outcome A

Positive Evidence

Memory 展现：

- 稳定；
- 可重复；
- 可信；

的正向影响。


后续：

由人工决定是否进入下一阶段。

---

## Outcome B

Negative Evidence

Memory：

- 无显著收益；
- 增加成本；
- 降低表现。


Phase 4 输出：

证据支持人工决定：

- 收缩方向；
- 暂停方向；
- 调整目标。

---

## Outcome C

Insufficient Evidence

证据不足。

允许补充验证。

但必须存在收敛条件：

- 样本规模；
- 验证次数；
- 时间窗口。


达到条件后：

若仍无法判断：

进入人工 Phase Review。

禁止无限延期。

---

# 5. Phase 4 Non-goals

Phase 4 不负责：

- Memory 自动升级；
- Trust 自动增长；
- Memory 生命周期管理；
- 自动冲突解决；
- 自动删除；
- Runtime 重构；
- Agent 架构修改。


---

# 6. Phase 4 Acceptance Criteria

Phase 4 完成必须回答：

## Q1

Memory 是否进入 Agent 行为？

对应：

S1 Evidence


---

## Q2

Memory 是否改善任务结果？

对应：

S3 Utility Evaluation


---

## Q3

改善是否可信？

对应：

S4 External Validation


---

## Q4

实验是否公平？

对应：

S2 Experiment Boundary


---

## Q5

如果 Memory 无价值怎么办？

对应：

Validation Outcome Model


---

# 7. Frozen Deliverables

Phase 4 最终产出：

1. Memory Utility Model

2. Memory Evidence Model

3. Context Experiment Boundary Specification

4. Correctness-aware Utility Evaluation Framework

5. External Validation Protocol

6. Validation Outcome Decision Model


---

# 8. Next Phase Boundary

Phase 4 Design Freeze 后：

进入：

Phase 4 PRD

然后：

Sliver Implementation Planning。


禁止：

在 Design Freeze 后重新扩大 Phase 4 范围。
