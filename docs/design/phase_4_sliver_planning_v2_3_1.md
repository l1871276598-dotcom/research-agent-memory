LAOS Phase 4 Sliver Planning v2.3.1

Memory Utility Validation Phase

Status: Design Freeze Candidate

Scope: Architecture Planning Only

---

1. Phase 4 第一性原理定义

根本问题

LAOS 不需要证明：

Memory 越多越好。

因为 Memory 可能：

* 被忽略；

* 没有实际帮助；

* 包含错误信息；

* 增加上下文噪声；

* 降低 Agent 表现。

因此 Phase 4 不解决：

如何让 Agent 使用更多 Memory。

而解决：

Memory 是否在受控条件下产生可验证、可信的正向影响。

---

2. Phase 4 核心目标

建立一个可证伪验证框架：

判断：

1. Memory 是否进入 Agent 行为；

2. Memory 是否改善任务结果；

3. 这种改善是否可信；

4. 如果没有改善，是否能够明确停止或收缩方向。

---

3. Memory Utility 定义

Memory Utility

定义：

Memory 对 Agent 任务结果产生的可验证影响。

基础表达：

Utility

=

Outcome(with Memory)

-

Outcome(without Memory)

---

该比较必须满足三个前提。

---

前提 1：Baseline 存在

必须存在：

无 Memory 条件。

否则无法判断：

Memory 是否造成增益。

---

前提 2：实验条件可比较

必须保证：

* 输入一致；

* 模型一致；

* 工具条件一致；

* 评价条件一致。

---

前提 3：Memory 具有可信基础

参与评价的 Memory 必须具有：

* 来源信息；

* 验证状态；

* 新鲜度信息；

* 冲突状态。

否则错误 Memory 可能产生假收益。

---

4. Utility 与 Correctness 分离原则

Phase 4 明确区分：

---

Utility

回答：

使用 Memory 是否改善任务结果？

评价：

* task success；

* error reduction；

* human correction reduction；

* output consistency。

---

Memory Correctness

回答：

Memory 内容本身是否可靠？

评价：

* provenance；

* source；

* freshness；

* validation status；

* contradiction status。

---

关系：

Correctness

决定

Memory 是否可信

Utility

决定

Memory 是否产生价值

---

禁止：

Memory 被使用

+

任务成功

↓

Memory 高价值

因为：

成功可能来自：

* 偶然因素；

* 其他上下文；

* 未触发错误场景。

---

5. Recovery Pattern 使用边界

Phase 4 仅继承 Recovery 的治理原则。

---

保留

Evidence

所有判断必须有证据。

---

Auditability

关键评价过程可追踪。

---

Explicit Boundary

实验条件和责任边界明确。

---

Review Gate

关键判断保留人工审核。

---

禁止迁移

Promotion

禁止：

高 Utility

↓

自动提升 Memory 等级

---

Trust 自动增长

禁止：

使用次数增加

↓

自动提高可信度

---

Recovery State Machine

禁止：

candidate

↓

approved

↓

promoted

---

Phase 4 不负责 Memory 生命周期治理。

---

6. Phase 4 Sliver 依赖结构

Phase 4 不采用线性 Pipeline。

采用证据依赖关系：

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

实际关系：

S0

↓

{S1, S2}

↓

S3

↓

S4

---

S0 — Memory Utility Foundation

目标

定义：

什么情况下 Memory 算产生价值。

---

必须定义：

* Utility Definition；

* Correctness Definition；

* Baseline Definition；

* Success Signal。

---

输出：

Memory Utility Model

---

S1 — Memory Evidence Model

目标

建立 Memory 证据基础。

回答：

Question 1

Memory 是否进入 Agent 行为？

---

Question 2

参与评价的 Memory 是否具有可信基础？

---

Usage Evidence

记录：

* selected；

* delivered；

* available；

* referenced。

---

Correctness Evidence

记录：

* provenance；

* source；

* freshness；

* validation status；

* contradiction status。

---

限制：

Phase 4 只记录已知状态。

不负责：

* 自动冲突检测；

* 自动修复；

* 自动淘汰。

---

输出：

Memory Evidence Record

---

S2 — Context Experiment Boundary

目标

定义公平比较条件。

---

原因：

没有实验边界，

不存在有效 baseline。

---

包括：

Memory Condition

定义：

Memory 如何参与实验。

---

Baseline Condition

定义：

无 Memory 条件。

---

Isolation Boundary

保证：

唯一变量：

Memory 是否存在。

---

Task Dataset Independence Principle

任务集合必须避免选择偏差。

要求记录：

* Task 来源；

* 选择规则；

* 覆盖范围；

* 是否由 Memory 建设方单独选择。

---

若：

任务集由 Memory 建设方单独选择，或明显偏向 Memory 优势场景：

则：

该结果不能作为 Outcome A 的充分支持证据。

最多：

作为探索性结果或 Outcome C 证据。

---

输出：

Context Experiment Boundary Specification

---

S3 — Correctness-aware Utility Evaluation

目标

回答：

Memory 是否改善任务结果？

---

核心：

Utility

=

Outcome(memory)

-

Outcome(no memory)

---

评价必须结合 Correctness Evidence。

---

Utility Evaluation Report 必须：

1. 按 Correctness 状态分层

包括：

Memory状态	处理

verified	可作为价值证据

unknown	观察结果

contradicted	不作为价值证明

---

2. 披露样本分布

必须报告：

* verified 比例；

* unknown 比例；

* contradicted 比例。

避免：

通过隐藏排除大量低可信样本制造正向结论。

---

3. Outcome 使用明确术语

使用：

* task success；

* task accuracy；

* human correction；

* consistency。

避免：

使用单独 "correctness score"。

防止：

任务结果正确性与 Memory 内容正确性混淆。

---

4. 评价要求

至少：

* 多任务；

* 多样本；

* 聚合分析。

---

输出：

Utility Evaluation Report

---

S4 — External Validation

目标

避免系统自我证明。

---

核心问题：

Utility 结果是否可信？

---

验证来源：

* Human review；

* External evaluator；

* Benchmark task。

---

要求：

评价标准不能完全由 Memory 系统自身定义。

---

输出：

Validation Evidence Package

---

7. Validation Outcome Model

Phase 4 是可证伪验证。

结果不预设。

---

Outcome A

Positive Evidence

Memory 显示：

* 稳定；

* 可重复；

* 可信；

的正向收益。

后续：

由人工决定是否进入下一阶段。

---

Outcome B

Negative Evidence

Memory：

* 无显著收益；

* 增加成本；

* 降低表现。

Phase 4 输出：

证据支持人工决定：

* 收缩范围；

* 暂停方向；

* 调整目标。

---

Outcome C

Insufficient Evidence

当前证据不足。

允许补充验证。

但是：

补充验证必须具有收敛条件：

包括：

* 预设样本规模；

* 预设验证次数；

* 预设时间窗口。

达到收敛条件后：

若仍无法判断：

进入人工 Phase Review。

不得无限延期。

---

8. Phase 4 Success Criteria

Phase 4 完成不是：

证明 Memory 有价值。

而是：

获得可信结论。

必须回答：

---

Q1

Memory 是否进入 Agent 行为？

对应：

S1

---

Q2

Memory 是否改善任务结果？

对应：

S3

---

Q3

改善是否可信？

对应：

S4

---

Q4

比较是否公平？

对应：

S2

---

Q5

如果 Memory 无价值怎么办？

对应：

Validation Outcome Model

---

9. Phase 4 Final Deliverables

Phase 4 输出：

不是 Runtime 功能。

而是验证基础。

包括：

1. Memory Utility Model

2. Memory Evidence Model

3. Context Experiment Boundary Specification

4. Correctness-aware Utility Evaluation Framework

5. External Validation Protocol

6. Validation Outcome Decision Model

---

10. Phase 4 当前状态

Phase 4 Sliver Planning v2.3.1

↓

Design Freeze

↓

PRD

↓

Sliver Implementation Planning

---

Design Freeze 前检查结果

项目	状态

第一性原理	✅

Utility 定义	✅

Baseline	✅

Correctness 分离	✅

Evidence 链	✅

可证伪出口	✅

Human authority	✅

Task independence	✅

Recovery 边界	✅

范围控制	✅
