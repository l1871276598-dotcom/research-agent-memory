> Released snapshot for repository auditability (Human Review Phase Gate 0 contract sync).
> Authoritative source lives in the local design vault; this copy is
> frozen as of 2026-07-13 and changes only through a new gate.

---
type: design-baseline
date: 2026-07-10
tags: '[laos, phase-5, baseline, fsm, claim-model, review-queue, design]'
status: approved-frozen
version: 4.3.2
approved: 2026-07-10（人工批准；修正案 A1/A2/A3 同时生效；审计缺口 G-1..G-5 知情接受）
amended: 2026-07-11（人工批准修正案 A4，版本 v4.3 → v4.3.1；同日人工批准修正案 A5，版本 v4.3.1 → v4.3.2；全文见 §14）
supersedes: Phase 5 Design Draft v4 (lost) + v4.1 patch + v4.2.1 amendment (consolidated)
related:
migration_source: 1-projects/LAOS/03-design/Phase 5 Design Baseline v4.3 — Consolidated Reconstruction.md
migration_status: canonical
migration_date: 2026-07-11
created: 2026-07-11
updated: 2026-07-11
---

# Phase 5 Design Baseline v4.3 — Consolidated Reconstruction (r3)

> **当前版本：v4.3.2**（2026-07-11 人工批准修正案 A4、A5 后两次 bump；文件名保留 v4.3 以维持既有 [[链接]]，引用本基线时以本行版本为准）。

> 类型：基线重建 + 整合 + 两项正式修正案。原 v4 基线丢失；本文档以 v4.1、v4.2.1、06 号契约为锚点重建为单一权威基线。
> 决策状态：**已批准冻结**（2026-07-10）。3 轮 Codex 红队 + 1 轮增量核验收敛后经人工终审。
> **批准效力**：人工批准本文档即同时批准 §14 的修正案 A1/A2（对 v4.2.1/06 的正式修订）。批准前，v4.2.1/06 原文优先；批准后，本文档为 Phase 5 唯一权威基线。

## 0. 重建声明

| 内容 | 来源 | 性质 |
|---|---|---|
| Reflection outcome_snapshot schema | v4.1 Patch 1 | 恢复 + lineage 字段（§4） |
| Human Review Queue schema | v4.1 Patch 2 | 恢复 + locator/结构化锚点（§6，向 v4.1 §8.2 增字段，additive 修正） |
| `==0`/`>0` 记号、`case_XX` 格式、memory.source 枚举、evidence_ref 格式 | v4.1 Minor Patches | 恢复（已冻结） |
| utility_evaluation v2、evaluation_id、enrichment 两字段 | v4.2.1 + 06（已实现） | 恢复；经 §14 修正案 A1/A2 扩展 |
| FSM Matrix 12 行 + 前置不变量 | §5 | 新冻结候选 |
| Claim model + 模板字面 + 字节格式 | §4 + 附录 A/C | 新冻结候选 |
| 双锚血缘（evaluation_id + snapshot digest） | §4.2 + 修正案 A1 | 对 v4.1 §5.1 的修正 + 对 06 的授权修订 |
| 三个新 ID、落盘/写序/崩溃模型、decision 最小状态机 | §8/§9/§7.1 | 新冻结候选 |
| 审计缺口 G-1/G-2 的规范性接受 | §11 | **规范决定**（非待定） |

## 1. 目标与第一性原理

```
utility_evaluation v2 (Phase 4)
  ↓ copy-only                     enriched_utility_evaluation（两字段，06 冻结）
  ↓ 确定性模板渲染（显式调用、无 LLM） reflection
  ↓ FSM 12 行查表（前置不变量 fail-closed） eligibility
  ↓ 仅 eligible、幂等、最后写         review_queue_request（不可变，恒 pending）
  —— Phase 5 边界 ——
  Human Review Phase：append-only decision 事件（§7.1 冻结最小语义，实现另期）
```

1. **Evidence before governance**：只搬运/分类既有事实。
2. **Human authority above automation**：创建不可变 pending 请求即止。
3. **确定性**：无 LLM、无随机、无时间戳；同输入 → 逐字节同 artifact、同 ID（字节格式见附录 C）。
4. **Fail-closed**：输入不一致、结构非法、落盘冲突 → 零写入报错。

### 1.1 "自动反思"禁令释义（解释性冻结）

禁止：自主/后台触发、LLM/生成式 claim、队列自动消费。允许：调用方显式触发的确定性 reflection builder（即 v4.1 冻结 pipeline 本体）。

### 1.2 H6 释义

`defined_before_run` 是必须携带的 bool 输入；值为 false 时合法进入 C 通道（producer 语义），"运行前定义"由该字段的语义承诺承载，不由 Phase 5 复核。

## 2. 审查对象冻结：evaluation disposition

队列审的是**一次评估的处置**：accept＝认可为有效证据 / reject＝否定该评估 / defer＝搁置 / revise＝要求重做实验。

硬边界：请求不含 memory_id/candidate_id；决策**不得映射**为单条 Memory 的生命周期动作（pack 级证据不支持归因，N-1/N-4）；Memory 决策唯一入口仍是 Phase 3 ReviewGate。`decision_options` 为完整枚举 `["accept","reject","defer","revise"]`，无排序、无默认、无推荐。

## 3. 数据权威性与既有契约衔接

- **权威性分类**（与 AGENTS.md "Markdown/JSONL 权威"规则的正确对位）：该规则约束的是 **memory 内容数据道**（vault Markdown / JSONL）。Phase 5 artifact 属于**运行证据数据道**（operational evidence，state_dir 本地），与既有 learning_runs/ 下的 outcome.json、comparison.json 同道同性质——JSON 是该道的既有形态，不与规则冲突。
- 道内再分两级：`enriched_utility_evaluation.json` 是**一次性落盘的根证据记录**（其输入 bundle 为内存态、不另持久化——E 一旦删除不可从别处重建，这是与 Phase 4 run artifacts 同级的证据地位）；reflection / eligibility / queue request 是**由 E 确定性可重放的派生记录**（删除可重建）。
- Phase 5 不读取任何 run.json（两套 run schema 之争不进入本基线，H19）。
- **无时间戳原则**：artifact 本体无 created_at；mtime 仅运维提示非审计证据；审计时间线 → 未来 append-only receipt log（G-3）。

## 4. Reflection artifact

### 4.1 Schema

```json
{
 "schema_version": 1,
 "template_version": 1,
 "reflection_id": "rf_<sha256>",
 "source_evaluation_id": "eval_<sha256>",
 "source_snapshot_digest": "snap_<sha256>",
 "outcome_snapshot": { "task_id": "", "without_memory_run_id": "", "with_memory_run_id": "",
   "validation_verdict": "A", "pack_utility_delta": 0,
   "evidence_composition": {"verified": 0, "unknown": 0, "contradicted": 0},
   "evidence_sufficiency": {"status": "sufficient", "verified_ratio": 0} },
 "claims": [], "uncertainties": [], "missing_information": [], "non_conclusions": []
}
```

copied-only、source-backed；禁止新事实/重算/聚合（v4.1 恢复）。

### 4.2 双锚血缘与硬等式（经修正案 A1 授权）

- `source_snapshot_digest = "snap_" + SHA-256(canonical(source_evaluation_snapshot))`（canonical 同 evaluation_id 配方）。digest 对象是 **snapshot 字段值本身**（不是 enriched 文件整体）；由于 enriched 恒为两字段且另一字段是 evaluation_id，元组 `(source_evaluation_id, source_snapshot_digest)` 即完整承诺 enriched 的全部逻辑内容——无需第三 hash，无歧义。
- **硬等式**（消费 enriched 时全部校验，任一失败即 fail-closed）：
  1. `reflection.source_evaluation_id == enriched.source_evaluation_id == enriched.source_evaluation_snapshot.evaluation_id`
  2. `重算 digest == reflection.source_snapshot_digest == 所在目录 digest 段`
  3. `outcome_snapshot` 各字段逐一等于 snapshot 对应字段
- **ID 语法校验**（用作路径段前强制）：`^eval_[0-9a-f]{64}$`、`^snap_[0-9a-f]{64}$`、`^rf_[0-9a-f]{64}$`、`^elig_[0-9a-f]{64}$`、`^rq_[0-9a-f]{64}$`；小写 hex 冻结；总路径超出文件系统限制 → fail-closed。
- 下游每次读取 reflection 不只比对 ID：重建预期对象并整体字节比较（附录 C）。

### 4.2.1 Caller-provided enriched 的再准入校验（信任边界闭合）

Phase 5 接受调用方给定的 enriched 输入前，必须全部通过：
1. **形状**：enriched 恰好两字段；snapshot 为对象。
2. **producer 类型闭包**：snapshot 逐字段满足 v2 producer 的全部类型/取值约束（schema_version==2、eval_ 正则、experiment 三字段非空字符串、delta 有限数值非 bool、三计数非负整数、thresholds 三键类型/范围合法、memory_record_source/staleness_warning 类型正确、无未知顶层字段）。
3. **派生复算**：按 §5.1 不变量 3–7 从 snapshot 事实重算 ratio/status/verdict 并要求一致。
4. **硬等式**：§4.2 全部等式。

**已知边界**（G-1 范围的明确化）：`evaluation_id` 的 preimage 含 first/second score，v2 输出只存 delta，故**链内无法验证 ID 的真实性**——一个各项自洽但 ID 为伪造哈希的 snapshot 在链内不可检出。真实性锚定走 G-1 冻结的跨层路径（comparison.json 复算）。此边界随 G-1 一并由人工知情接受。

### 4.3 Claim model

结构：`{"claim_id","statement","evidence_ref"}`；non_conclusions 例外（§4.4）。

- `claim_id` 为 reflection 内局部模板键。
- `evidence_ref` 白名单 = 附录 A 精确路径集；根仅 `outcome_snapshot.*`（对象内解析）与 `source_evaluation_snapshot.*`（经 §9 目录内 enriched 文件解析）。ref 缺失/类型不符/statement 插值与 ref 值不符 → fail-closed。
- **模板全集 = 输出全集**（封闭）：无条件模板必现；条件模板当且仅当条件成立。数组顺序 = 附录 A 列表顺序（冻结）。
- 插值渲染规则见附录 C（canonical JSON token，浮点不舍入、不改写）。

清单：CL-1 delta、CL-2 verdict、CL-3 构成、CL-4 充分性、CL-5 阈值三值；U-1 unknown>0、U-2 contradicted>0、U-3 staleness（当前恒真，诚实记录）；M-1 caller_provided（当前恒真）。字面见附录 A。

### 4.4 non_conclusions：契约级否定，独立证据类型

N-1..N-4 不是数据字段可证明的事实，而是**契约边界的复述**。结构改为：

```json
{"claim_id": "N-1", "statement": "", "contract_ref": "baseline-v4.3 §2"}
```

`contract_ref` 白名单（附录 A）：指向本基线或 06 号契约的条款。N-* 不服从"单一数据字段证据"规则，也不参与 evidence_ref 解析校验；其 statement 字面照常随 template_version 冻结。

### 4.5 禁词不变量（封闭集合 + 精确扫描域）

- **封闭禁词集**（冻结；为既有两套测试词汇的并集）：`{"trust","ranking","weight","per_memory_utility","recommendation","remove","delete"}`。扩集须 bump 本基线版本。
- **扫描域**：(a) 全部 Phase 5 artifact 的 JSON **键名**（case-fold 子串匹配）；(b) 模板产出的**值文本**必须与附录 A 冻结字面 + 合法插值完全一致（整体相等断言，自然排除任何越界词）。**不扫描** caller 提供的数据值（task_id、run_id 等）——它们是被搬运的事实，不是 Phase 5 的表达。N-* 字面在附录 A 允许列表内。

## 5. Eligibility 与 FSM Matrix

### 5.1 前置不变量（进入矩阵前 fail-closed；与 producer 语义严格对齐）

1. `validation_verdict ∈ {"A","B","C"}`；`status ∈ {"sufficient","insufficient"}`（枚举冻结）；
2. 三计数非负整数（其和 total 的自洽已在 producer 强制——S5.1e，commit 83cd20c）；
3. **精确复算，零容差**（复算即重跑 producer 同一算式，IEEE-754 双精度下同式同入必同出，比较用 canonical token 相等）：
   - `sum == verified + unknown + contradicted`；
   - `sum == 0` ⇒ `ratio == 0.0` 且 `status == "insufficient"`（producer 对空构成恒判不足，**与阈值无关**）；
   - `sum > 0` ⇒ `ratio == verified / sum`（token 相等，无 ±ε）且 `status == "sufficient" ⇔ ratio >= verified_ratio_min`；
4. `verdict ∈ {A,B}` ⇒ `status == "sufficient"` 且 `defined_before_run == true`；
5. `verdict == A ⇔ pack_utility_delta > utility_delta_min`（A/B 域内）；`verdict == B ⇔ delta <= utility_delta_min`（含相等）；
6. `verdict == C` ⇔ `defined_before_run == false` 或 `status == "insufficient"`；
7. outcome_snapshot 与经 digest 校验的 snapshot 逐字段一致（token 相等）。

任一不满足 → 报错，不落任何 case。复算顺序冻结：先 3（构成与充分性）再 5/6（verdict），与 producer 执行序一致；全部由 snapshot 事实决定，无外部输入。

### 5.2 Schema 与 reason

```json
{ "schema_version": 1, "eligibility_id": "elig_<sha256>", "source_reflection_id": "rf_<sha256>",
  "fsm_matrix_version": 1, "matched_case": "case_01", "eligibility_status": "eligible", "reasons": [] }
```

- `fsm_matrix_version` 冻结内容 = 12 行裁决 + 附录 B 全部 reason 字面。任何变化 bump + 重批。
- 矩阵必须为显式 12 行数据表（禁止 if/else 隐式编码），测试逐行锁死。
- C 行 reasons 精确区分 `R-C1 threshold_not_frozen` / `R-C2 evidence_insufficient`（可并列，禁止模糊"或"）。
- reasons 顺序冻结：verdict reason（R-A|R-B|R-C1,R-C2）→ R-U → R-X。

### 5.3 FSM Matrix v1（12 行，红队已证全部可达、互斥全覆盖）

维度：`verdict` × `contradicted(==0/>0)` × `unknown(==0/>0)`。

| case | verdict | contradicted | unknown | eligibility |
|---|---|---|---|---|
| case_01 | A | ==0 | ==0 | eligible |
| case_02 | A | ==0 | >0 | eligible |
| case_03 | A | >0 | ==0 | eligible |
| case_04 | A | >0 | >0 | eligible |
| case_05 | B | ==0 | ==0 | eligible |
| case_06 | B | ==0 | >0 | eligible |
| case_07 | B | >0 | ==0 | eligible |
| case_08 | B | >0 | >0 | eligible |
| case_09 | C | ==0 | ==0 | ineligible |
| case_10 | C | ==0 | >0 | ineligible |
| case_11 | C | >0 | ==0 | ineligible |
| case_12 | C | >0 | >0 | ineligible |

- **eligible 唯一含义**："存在 producer 已裁决的评估结论（超过/未超过冻结阈值），可供人类作 disposition 决定"。A=delta 超过冻结阈值；B=未超过（含相等）。**不使用** "positive utility"/"no-improvement" 之类可能陈述假事实的措辞（阈值可为负）。
- **C 一律 ineligible**：无可处置结论；artifact 落盘可审计；triage 提醒属运维通道，本 Phase 不负责（G-4）。
- contradicted/unknown 只进 reasons/evidence_summary，不翻转 eligible。

## 6. Human Review Queue request（不可变请求 + locator + 结构化锚点）

```json
{
 "schema_version": 1,
 "template_version": 1,
 "review_queue_item_id": "rq_<sha256>",
 "source_eligibility_id": "elig_<sha256>",
 "source_evaluation_id": "eval_<sha256>",
 "source_snapshot_digest": "snap_<sha256>",
 "experiment": {"task_id": "", "without_memory_run_id": "", "with_memory_run_id": ""},
 "status": "pending",
 "summary": "",
 "evidence_summary": {"validation_verdict": "A", "pack_utility_delta": 0,
   "verified": 0, "unknown": 0, "contradicted": 0},
 "missing_information": [],
 "decision_options": ["accept", "reject", "defer", "revise"]
}
```

- **locator**：`source_evaluation_id + source_snapshot_digest` 使 `rq → evaluations/{eval}/{digest}/` 目录可直接计算，链路 `rq → elig → rf → enriched` 可达。写入前校验 locator 与 eligibility/reflection 的锚点一致。
- **结构化 experiment 块**：task_id 与两个 run_id 为结构化字段（copy-only 自 snapshot）；`summary` 为附录 A SUMMARY 模板对这些结构化字段的渲染，**加载时必须重渲染并整体比较**——不从自由文本反解析事实。
- `status` 恒 `"pending"`（不可变请求）；`template_version` 冻结 SUMMARY 字面。
- 仅 eligible 生成；每个 `source_eligibility_id` 至多一个请求。
- **全字段来源与形状冻结**（同一 rq ID 只对应一种合法字节）：
  - `evidence_summary.validation_verdict/pack_utility_delta/verified/unknown/contradicted` ← reflection.outcome_snapshot 对应字段逐一 copy；
  - `missing_information` ← reflection.missing_information **原对象数组逐字节 copy**（元素形状 `{claim_id, statement, evidence_ref}`，顺序同 reflection）；
  - `decision_options` 恒为 `["accept","reject","defer","revise"]`（顺序冻结）；
  - 全部键顺序由 canonical 序列化决定（附录 C），无自由度。

### 6.1 与 v4.1 §8.2 的差异声明（完整清单）

1. 字段名：`review_id` → `review_queue_item_id`（v4.2.1 §2.5 已冻结的改名，非本基线新增）。
2. 新增字段：`schema_version`、`template_version`、`source_evaluation_id`、`source_snapshot_digest`、`experiment`。
3. status 语义：v4.1 的后续状态 `accepted/rejected/deferred/revised` **不再存储于本文件**——request 不可变恒 `"pending"`，后续状态是 request + decision 事件的派生视图（§7.1 给出 action→status 词形映射）。v4.1 "Phase 5 只允许 pending、不写入后续状态"的边界原样保留且更严格。
4. `missing_information` 元素由（v4.1 未定义形状的）数组明确为 reflection 条目对象。

## 7. Human Authority Boundary

Phase 5 允许（白名单）：读取调用方给定的 enriched（先过 §4.2 全部硬等式与 ID 语法校验）；按确定性路径读取自己预期 ID 的既有 artifact 并做字节比较；§9 语义下的原子发布。

硬禁令：不枚举 queue 目录、不读 decision、不按 **queue/decision 状态**分支业务逻辑（注：pipeline 读取**自产的** `eligibility_status` 决定是否写 queue 属于 §5 冻结职责，不在此禁令内）、不注入 active context、不调用 ReviewGate/lifecycle/CandidateStore、不改 memory/candidate/threshold、不生成治理字段、不自触发、无 LLM。Rule Library 继续延期。

### 7.1 Decision 最小状态机（本基线冻结语义；实现属 Human Review Phase）

> **（经修正案 A5 supersede，2026-07-11 人工批准）**：本节的字段名（operator/decided_at）、幂等规则（"action 相同即幂等、先写者为准"）与未尽落盘语义已由 A5 修订，冲突处以 A5 为准——见 §14-A5；权威全文 = [[Human Review Phase 设计基线 v1]]（v1.7 approved-frozen）§1–§9。以下原文保留作历史记录；其中状态机、seq∈{1,2}、defer 仅 seq1、create-if-absent、append-only、request 永不改写经 A5-7 原样保留。

- **词形映射**（对齐 v4.1 冻结的状态词）：action `accept/reject/defer/revise` → 派生状态 `accepted/rejected/deferred/revised`。
- decision artifact（append-only）：`{decision_seq ∈ {1,2}, review_queue_item_id, action, operator, reason, decided_at}`。**decision 属人类事件道，必须携带 timestamp（04 号契约 transition 三要素 + 时间）**；无时间戳原则（§3）只约束 Phase 5 确定性 artifact，不约束人类事件。
- **事件身份与并发单终态**：decision 文件名 = `{review_queue_item_id}.decision_{seq}.json`，以 create-if-absent 发布——同一 seq 天然全局唯一，并发写入只有一个成功（文件系统仲裁），不存在双终态。
- 状态机：`pending →(seq1: accept|reject|revise)` 终态；或 `pending →(seq1: defer)→ deferred →(seq2: accept|reject|revise)` 终态。defer 仅允许出现在 seq1；seq2 不得为 defer。
- 幂等与冲突：写 seq N 时若同名文件已存在——action 相同 → 幂等返回既有 decision（record 的 operator/reason 以先写者为准）；action 不同 → 拒绝（H20 延伸）。
- request 的"当前状态" = request + decisions 的派生视图；request 文件永不改写。

## 8. 确定性 identity

配方：`<prefix>_ + SHA-256(canonical JSON of payload)`，全长不截断。

| artifact | prefix | payload（且仅含） |
|---|---|---|
| reflection | `rf_` | {schema_version, template_version, source_evaluation_id, source_snapshot_digest} |
| eligibility | `elig_` | {schema_version, fsm_matrix_version, source_reflection_id} |
| review_queue_request | `rq_` | **{source_eligibility_id}** |

- rq payload **不含版本号**（修复 R2-4 重复 pending）：同一 eligibility 的请求路径恒定；queue schema/template 升级不改变 rq ID。版本记录在文件内；重放遇版本不同 → fail-closed（交人工迁移决定），绝不生成第二个 pending。
- rf/elig 含版本号是安全的：其文件名在 `evaluations/{eval}/{digest}/` 下**固定**（reflection.json / eligibility.json），版本变化不产生并存副本，只触发同路径 fail-closed。
- 防静默漂移：附录 A/B 字面即版本的冻结内容；改字面不 bump = 契约违规，且必被"重建预期对象字节比较"检测。

## 9. 落盘、发布语义与崩溃模型

```
state_dir/
  evaluations/{evaluation_id}/{source_snapshot_digest}/
    enriched_utility_evaluation.json
    reflection.json
    eligibility.json
  review_queue/
    {review_queue_item_id}.json
```

- **文件系统前置条件**（接口契约）：state_dir 必须位于**本地、支持 POSIX link/fsync 语义的文件系统**，且不得处于 iCloud/File Provider 等同步根之下（继承既有工程边界，见 `src/memory.py` 对本地 state 目录的约束）。能力不满足 → 启动即 fail-closed。
- **类型化 preflight（零写入）**：任何写之前，只读检查全链现状并按 `eligibility_status`（若 El 已存在）选择合法语言：
  - eligible 链合法集：`{∅, [E], [E,R], [E,R,El], [E,R,El,Q]}`（[E,R,El] 是 crash 前缀，允许补 Q）；
  - ineligible 链合法集：`{∅, [E], [E,R], [E,R,El]}`（[E,R,El] 是**终态**；`ineligible ∧ Q 存在` = corruption → fail-closed）；
  - 任何非前缀组合（存在下游缺上游）→ 零写入报错，不"反向愈合"。
  - 既有正式文件一律重建预期对象做**字节比较**（含版本、内部 ID、文件名、目录 digest 段）；相等 → 幂等继续；不等 → 报错。
- **temp 生命周期**：temp 命名 `.tmp.<target-basename>.<nonce>`（`.` 前缀；nonce 仅入文件名不入内容，不破坏 artifact 确定性）。temp **永远不是 artifact**：preflight 遇到任何 `.tmp.*` 一律视为上次中断残留，先 unlink 清理再评估链状态（temp 清理不属于"写入"，不违反零写入原则）。
- **发布序列（no-replace，逐步冻结）**：① 同目录写 temp；② `fsync(temp)`；③ `link(temp, target)`——目标已存在则失败（原子 create-if-absent，target 只能以完整字节出现，截断不可能）；④ `unlink(temp)`；⑤ `fsync(所在目录)`。目录创建时：`mkdir` 后 `fsync(父目录)`，自根向叶逐级。禁止 `os.replace` 触碰任何既有目标。link 失败于"目标已存在" → 走幂等字节比较路径。
- **崩溃保证范围**：在上述 fsync 链语义内，任一崩溃点的磁盘状态 ∈（合法前缀集 ∪ 附带若干 `.tmp.*` 残留 ∪ ③④ 之间的 target+temp 并存）——三者都被 preflight 归一（清 temp → 前缀评估 → 从缺失第一环重放）。超出 fsync 语义的破坏（硬件回写缓存丢失、位腐）不在保证内，由字节校验兜底检出 → fail-closed。
- **queue 最后写**：queue request 落盘前，链路对人类不可见。
- **隔离前置条件**：一个 state_dir = 一个隔离域（workspace/scope 由调用方保证，G-5）。

## 10. 明确不做

不加 previous_evaluation_id/registry/enrichment 第三字段；不改 utility 公式、composition、verdict、lifecycle、ReviewGate；不实现队列消费与 decision 写入（Human Review Phase）；不做规则学习/自动 promotion/deletion/后台自触发/LLM；不做跨 evaluation 聚合、排序、时效提醒；memory.source 仅 caller_provided。

## 11. 审计缺口的规范性决定（本基线正式裁决，随批准生效）

| # | 缺口 | **决定** |
|---|---|---|
| G-1 | Phase 5 链内不能独立复算 evaluation_id（preimage 含 first/second score，v2 只存 delta） | **接受**。冻结审计路径：经 `experiment.*_run_id → learning_runs/{second_run_id}/comparison.json` 跨层复算。该文件持久存在。 |
| G-2 | per-memory provenance 缺失（仅聚合计数） | **接受**。pack 级评估固有边界（06 显式非目标）；N-1/N-4 使人类知情；memory 级审查走 Phase 3 通道；run_snapshot 版本另期解冻。 |
| G-3 | 无审计时间线 | **接受**，受 §3 无时间戳原则约束；receipt log 另立设计。 |
| G-4 | C 类无主动 triage | **接受**；运维通道职责。 |
| G-5 | 跨域隔离靠调用方 | **接受**为接口前置条件。 |

## 12. 验收框架

1. Reflection 逐字节可重放；四组数组与附录 A 全集、顺序、字面、插值一致；evidence_ref 全解析且值一致；contract_ref 在白名单；digest 硬等式全过。
2. FSM 前置不变量 8 条逐条 fail-closed（含 A/B ⇔ delta-阈值关系、ratio/status 复算）；显式 12 行表与 §5.3 逐行相等；12 组合穷举唯一命中。
3. ineligible：链终止于 eligibility，落盘可审计。
4. queue request：不可变（byte-compare 幂等 / 冲突报错 / 绝不覆盖）；locator 可反向寻址整链；summary 重渲染相等；每 eligibility 至多一个；schema 升级不产生第二 pending（版本不匹配 fail-closed）。
5. 三个 ID 确定性；snapshot 实例差异（同 evaluation_id 不同 metadata）→ 不同 digest → 不同目录与 rf_ 链。
6. preflight：非前缀组合零写入报错；崩溃任意点重放收敛。
7. 禁词：键名扫描 + 模板输出整体相等断言（§4.5 语义）。
8. ID 语法正则与路径长度 fail-closed。

## 13. 后续 sliver 划分

```
S5.2 reflection builder（§4 + 附录 A/C + 硬等式）
S5.3 eligibility + FSM v1（§5 + 附录 B）
S5.4 queue request + §9 全部发布/preflight/崩溃语义
```

依赖单向，每个独立 RED→GREEN。（S5.1e producer total 不变量已完成，commit 83cd20c。）

## 14. 正式修正案（随本基线批准同时生效）

### A1 — 对 v4.2.1 §2.4 / 06 号契约 §4 的修订：snapshot 实例锚授权

- 背景：`evaluation_id` 按设计排除 adapter metadata 与未晋升 threshold，故**不唯一决定** snapshot 实例（红队探针证实）。
- 修订：授权 Phase 5 消费侧以 `snap_<sha256>` digest 作为 snapshot 实例锚，用于 reflection/queue 字段与目录段。**enrichment artifact 本体保持两字段不变**；06 的"enrichment 不得携带 source_evaluation_hash"禁令不变（digest 位于消费侧 artifact，非 enrichment 字段）。
- 06 号契约文件相应增补一节（批准后执行，bump 其 Version 至 0.2.0）。

### A2 — 对 06 号契约 §2/§3 的修订：producer 计数完整性

- 背景：`summary.total` 不参与 identity 但决定 ratio/sufficiency/verdict，构成"同 identity facts 不同 derived 结论"漏洞，违反 06 §3 的确定性论断。
- 修订：`build_utility_evaluation()` 强制四计数为非负整数且 `total == verified+unknown+contradicted`（**已实现并测试**，commit 83cd20c，684 passed）。06 文件补录该 gate（批准后执行）。

### A3 — 对 v4.2.1 §3 / 06 号契约非目标措辞的澄清修订

- 背景："No automated reflection" 原文未区分"自主/生成式"与"显式触发的确定性渲染"，与 v4.1 冻结的 Reflection pipeline 存在表面解释冲突（红队 r2/r3 两轮均指出）。
- 修订：06 v0.2.0 非目标清单中该句替换为本基线 §1.1 的三分释义（禁自主触发、禁 LLM/生成式 claim、禁队列自动消费；允许显式触发的确定性 builder）。v4.2.1 归档不改文，以本基线为准。

### A4 — 对 §7 的修订：preflight 限定清理例外（v4.3.1，2026-07-11 人工批准）

- 背景：§9 强制 preflight 清理一切 `.tmp.*` 残留，而 temp 与 target 同目录为 §9 冻结要求，`review_queue/` 内的 temp 清理必然枚举该目录，与 §7"不枚举 queue 目录"硬禁令正面冲突，无实现层回避路径（S5.4 计划红队 r2 裁定为实质修订，r3-ext/r6/r7 三轮收紧文本后过审；全程见 [[S5.4 计划对抗审查记录]]）。
- 修订（§7 增补限定例外）：Phase 5 持久化层的 preflight 阶段，为执行 §9 强制的 `.tmp.*` 清理，允许枚举 `review_queue/` 目录。枚举所得名称仅可用于两个操作：`.tmp.*` 前缀匹配判定，及匹配后的直接 unlink；不得解析名称中的 ID、与预期 rq ID 比较、记录、返回或据此进行业务分支。对不匹配条目不得 stat/read/据其存在性分支业务逻辑。（temp 名内嵌 target basename，构成潜在 queue 信息通道，本例外不授予读取该信息的权利。）§7 其余禁令（不读 decision、不按 queue/decision 状态分支）不变。
- 生效：随本修正案人工批准即生效，基线版本 v4.3 → v4.3.1。

### A5 — 对 §7.1 的修订：Human Review decision 完整语义（v4.3.2，2026-07-11 人工批准）

- 背景：[[Human Review Phase 设计基线 v1|Human Review Phase 设计基线 v1]].7 经八轮定向红队（r1–r8，终局 CONVERGED 0C/0M/0m）收敛后人工批准（approved-frozen）；§7.1 原最小状态机在字段语义、幂等规则与落盘/并发语义上不足以支撑实现。**A5 权威全文 = [[Human Review Phase 设计基线 v1]] §9（11 条正式提案文本），冻结语义正文 = 同文件 §1–§8**；本节为入册摘要，冲突处以该文件为准。
- 修订要点（对应 A5-1..A5-11）：
  1. 字段更名：`operator` → `operator_claim`（调用方自述，系统不验证）；`decided_at` → `recorded_at`（writer 非权威墙钟，词法冻结 `YYYY-MM-DDTHH:MM:SSZ`，不参与排序/幂等/派生/授权；时钟不可用 → 拒写）。
  2. 幂等规则替换：原"action 相同 → 幂等（operator/reason 以先写者为准）"**废止** → 完整命令语义投影（rq_id, seq, action, operator_claim, reason；精确 canonical/code-point 相等，禁止归一化）严格相同才无写入幂等返回**既有持久事件原样**；任一字段异 → 确定冲突拒绝。
  3. schema 冻结：8 键封闭集合，含 `schema_version`(恒 1) 与 `decision_digest = "dcd_" + SHA-256(canonical(其余 7 键))`（仅一致性检测，无防篡改承诺）。
  4. 存储归属：`state_dir/decisions/` 独立 namespace；文件名 `{rq_id}.decision_{seq}.json` 不变。
  5. 管辖权前置（G8）：写入前经独立只读 seam 校验冻结链；不可绑定 → 零 decision。
  6. 效力边界：decision 仅承载 evaluation disposition 证据准入语义（G1-B′ 零自动效力）；correction 挂起及强制重开条件入册。
  7. 其余 §7.1 语义（状态机、seq∈{1,2}、defer 仅 seq1、create-if-absent 仲裁、append-only、request 永不改写）原样保留。
  8. 枚举授权（对 §7 的限定例外，与 A4 同型）：Human Review Subsystem 获 `review_queue/` **只读枚举**权，仅用于 review.list 呈现与管辖权校验；Phase 5 Producer 的 §7 禁令原样不变。
  9. Q→D 持久交接：decision 发布前 writer 补做幂等 `fsync(review_queue/)`；`D ∧ ¬Q` 为 corruption，fail-closed 交人工。
  10. 持久观察与并发采集：可见 final 在目录 fsync 前不得视为 committed；屏障仅 fsync 已存在且类型合法的 namespace（合法缺失 = 空集合）；单 rq 先 seq2 后 seq1；全局有界两轮关系校验采集。
  11. decisions/ bootstrap（并发 mkdir-or-verify + 每 writer 无条件 `fsync(state_dir)`）；发布成功以 post-publish final readback 为准；双 namespace 对账，孤儿 D 必达呈报。
- 生效：随本修正案人工批准即生效，基线版本 v4.3.1 → v4.3.2。

---

## 附录 A — 模板字面（template_version = 1 冻结；插值规则见附录 C）

| key | statement 字面 | 证据 |
|---|---|---|
| CL-1 | `pack_utility_delta is {delta}.` | ref `outcome_snapshot.pack_utility_delta` |
| CL-2 | `validation_verdict is {verdict}.` | ref `outcome_snapshot.validation_verdict` |
| CL-3 | `evidence composition is verified={v}, unknown={u}, contradicted={c}.` | ref `outcome_snapshot.evidence_composition` |
| CL-4 | `evidence sufficiency is {status} with verified_ratio {ratio}.` | ref `outcome_snapshot.evidence_sufficiency` |
| CL-5 | `thresholds were utility_delta_min={a}, verified_ratio_min={b}, defined_before_run={d}.` | ref `source_evaluation_snapshot.thresholds` |
| U-1 | `{u} memory record(s) are unverified.` | ref `outcome_snapshot.evidence_composition.unknown` |
| U-2 | `{c} memory record(s) are contradicted.` | ref `outcome_snapshot.evidence_composition.contradicted` |
| U-3 | `memory records may be stale; staleness_warning is true.` | ref `source_evaluation_snapshot.staleness_warning` |
| M-1 | `memory records were caller_provided and not re-verified from a run snapshot.` | ref `source_evaluation_snapshot.memory_record_source` |
| N-1 | `pack-level utility delta cannot attribute contribution to any individual memory record.` | contract_ref `baseline-v4.3 §2` |
| N-2 | `the validation verdict does not justify any memory lifecycle action.` | contract_ref `baseline-v4.3 §2` |
| N-3 | `evidence counts do not constitute reliability or weighting of memory records.` | contract_ref `contracts/06 §3-exclusions` |
| N-4 | `coverage between memory_records and used_memory_ids was not checked; no per-memory attribution is provided.` | contract_ref `contracts/06 §2-not-gates` |
| SUMMARY | `experiment {task_id}: runs {without_run_id} -> {with_run_id}, verdict {verdict}.` | 锚 queue.experiment + evidence_summary.validation_verdict |

contract_ref 白名单 = {`baseline-v4.3 §2`, `contracts/06 §3-exclusions`, `contracts/06 §2-not-gates`}。

## 附录 B — Eligibility reason 字面（fsm_matrix_version = 1 冻结）

| key | 字面 | 触发 |
|---|---|---|
| R-A | `verdict A: pack_utility_delta exceeds the frozen utility_delta_min; disposition review is available.` | verdict==A |
| R-B | `verdict B: pack_utility_delta does not exceed the frozen utility_delta_min; disposition review is available.` | verdict==B |
| R-C1 | `verdict C: thresholds were not frozen before the run (defined_before_run is false).` | C 且 defined_before_run==false |
| R-C2 | `verdict C: evidence sufficiency is insufficient.` | C 且 status==insufficient |
| R-U | `unknown records present: unknown={u}.` | unknown>0 |
| R-X | `contradicted records present: contradicted={c}.` | contradicted>0 |

reasons 顺序：verdict reason（C 时 R-C1 先于 R-C2，按各自触发）→ R-U → R-X。

## 附录 C — 字节格式与插值规则（canonicalizer_version = 1 冻结）

1. **artifact 文件字节** = `canonical JSON`：`sort_keys=True`、separators `(",",":")`、`ensure_ascii=True`、`allow_nan=False`、UTF-8 无 BOM、**无尾随换行**。既有文件比较 = **字节相等**。
2. **数值算法（规范性）**：浮点渲染 = IEEE-754 double 的**最短往返十进制表示**（shortest round-trip，即 CPython ≥3.1 的 `float.__repr__`/`json` 所实现的算法；整数按十进制原样）。该算法为 canonicalizer_version=1 的冻结内容——若未来 Python 改变输出，实现必须自带 shim 维持本算法或经人工批准 bump canonicalizer 版本。**golden bytes 向量**（S5.2 测试必须锁定）：`0.3→"0.3"`、`0.1+0.2 的结果→"0.30000000000000004"`、`1/3→"0.3333333333333333"`、`-0.0→"-0.0"`、`1e300→"1e+300"`、`0.5→"0.5"`、`0→"0"`（int）。不舍入、不改写、不归一化 `-0.0`。
3. **插值 token**：`{x}` 渲染该值的 canonical JSON token——数字按上条；布尔 `true`/`false`；字符串为**带双引号的 JSON 转义形式**（含 task_id/run_id；`ensure_ascii` 使非 ASCII 呈 `\uXXXX`，嵌入 statement 后文件字节出现双层转义——**有意结果**，机器重放精确，人类阅读靠渲染工具）。
4. **数组顺序（全部 artifact）**：claims=[CL-1..CL-5]、uncertainties=[U-1..U-3]、missing_information=[M-1]、non_conclusions=[N-1..N-4]（条件模板缺席不改相对顺序）；eligibility.reasons 见附录 B；queue.missing_information 同 reflection 顺序；queue.decision_options=[accept,reject,defer,revise]。对象键序由 sort_keys 决定，无自由度。
5. digest/ID 计算与文件字节使用同一 canonical 规则（同一 canonicalizer_version）。
