# LAOS Phase 4 Sliver Implementation Planning

## Memory Utility Validation Phase

**Status:** Implementation Planning Draft

**Scope:** Implementation Planning Only

---

# 1. Implementation Objective

Phase 4 实现目标：

不是构建新的 Memory Runtime。

不是自动提升 Memory。

不是改变 Agent 架构。

而是：

> 将 Phase 4 Design Freeze 中定义的 Memory Utility Validation Framework 转化为可验证、可测试的工程能力。

---

核心目标：

建立：

- Memory Evidence；
- Experiment Boundary；
- Utility Evaluation；
- External Validation；

所需的最小实现基础。

---

# 2. Implementation Principles

## 2.1 Minimal Change

遵守：

代码最少原则。

优先：

- 复用已有结构；
- 扩展已有 schema；
- 使用已有 outcome 机制；
- 使用已有 safety boundary。


禁止：

- 新建无使用者抽象；
- 重构无关模块；
- 创建新的 Runtime。


---

## 2.2 Evidence First

所有实现必须服务：

证据产生。

不是服务：

功能展示。


---

## 2.3 No Automatic Governance

Phase 4 实现禁止：

- 自动提升 Memory；
- 自动改变 Trust；
- 自动删除 Memory；
- 自动修改 Memory 生命周期。


---

## 2.4 Test Before Implementation

每个 Sliver：

先定义：

- 验收行为；
- 失败测试；
- 验证标准。

再实现。

---

# 3. Frozen Implementation Boundary

Phase 4 只实现：

S0-S4。

不实现：

Phase 5 能力。

---

# 4. Implementation Dependency Graph

不是线性 Pipeline。

采用：

```
         S1
  Memory Evidence Model
          ↑
          |
S0 Utility Foundation
          |
          ↓
  S2 Experiment Boundary
          |
          ↓
  S3 Utility Evaluation
          |
          ↓
  S4 External Validation
```

实际实施关系：

```
S0
↓
S1 + S2
↓
S3
↓
S4
```

---

# 5. Sliver Implementation Plan

---

# S0 — Memory Utility Foundation

## Objective

建立 Memory Utility 基础定义。


## Implementation Scope

包括：

- Utility Model；
- Baseline Model；
- Success Signal Definition。


---

## First Test

验证：

系统能够明确区分：

With Memory

和

Without Memory。


---

## Output

Memory Utility Schema / Definition Artifact


---

## Not Included

不实现：

自动 Utility 判断。

---

# S1 — Memory Evidence Model

## Objective

建立 Memory 证据记录能力。


---

## Implementation Scope

记录：

## Usage Evidence

字段：

- selected；
- delivered；
- available；
- referenced。


---

## Correctness Evidence

字段：

- provenance；
- source；
- freshness；
- validation status；
- contradiction status。


---

## Constraints

只记录已有状态。


禁止：

- 自动检测冲突；
- 自动修复；
- 自动淘汰。


---

## First Test

验证：

Evidence Record 能完整保存 Memory 状态。


---

## Output

Memory Evidence Record


---

# S2 — Context Experiment Boundary

## Objective

建立可比较实验条件。


---

## Implementation Scope

定义：

- Memory Condition；
- Baseline Condition；
- Isolation Boundary。


---

## Task Dataset Independence

记录：

- task 来源；
- task 选择规则；
- task 覆盖范围；
- 是否独立选择。


---

## First Test

验证：

Memory 与 No Memory 条件仅存在目标变量差异。


---

## Output

Experiment Boundary Specification


---

# S3 — Correctness-aware Utility Evaluation

## Objective

生成 Utility Evaluation。


---

## Implementation Scope

支持：

Outcome(with Memory)

vs

Outcome(without Memory)


---

## Required Evaluation

必须：

结合 Correctness Evidence。


报告：

verified:

可作为价值证据。


unknown:

观察结果。


contradicted:

不能作为价值证明。


---

## Required Reporting

必须披露：

- verified 比例；
- unknown 比例；
- contradicted 比例。


---

## First Test

验证：

Evaluation Report 能区分：

Memory 使用效果

与

Memory 内容可信度。


---

## Output

Utility Evaluation Report


---

# S4 — External Validation

## Objective

降低系统自证风险。


---

## Implementation Scope

支持：

- Human Review Input；
- External Evaluation Result；
- Benchmark Result。


---

## First Test

验证：

External Evidence 可以独立进入 Validation Package。


---

## Output

Validation Evidence Package


---

# 6. Testing Strategy

Phase 4 测试重点：

不是：

代码覆盖率最大化。

而是：

验证证据链完整。


---

测试层级：

## Level 1

Schema Validation

验证：

数据结构正确。


---

## Level 2

Behavior Test

验证：

行为符合定义。


---

## Level 3

Boundary Test

验证：

失败条件正确处理。


---

## Level 4

Integration Test

验证：

S0-S4 输出可以连接。


---

# 7. Explicit Non-Goals

Implementation 阶段禁止：

- Memory Promotion；
- Trust 自动增长；
- Lifecycle Feedback；
- 自动规则学习；
- Agent Runtime 改造；
- 大规模架构重构。


---

# 8. Completion Criteria

Phase 4 Implementation Planning 完成标准：

## Criterion 1

每个 Sliver 有：

- Objective；
- Scope；
- Test；
- Output。


---

## Criterion 2

不存在：

无验证目标的代码。


---

## Criterion 3

不存在：

超出 Phase 4 范围的功能。


---

## Criterion 4

所有实现均可通过：

Evidence → Validation

链路解释。


---

# 9. Next Step

Implementation Planning 完成后：

进入：

TDD Implementation。


流程：

```
Test
↓
Minimal Implementation
↓
Verification
↓
Review
```

继续遵守：

- 最小修改；
- 证据优先；
- 人工治理边界。
