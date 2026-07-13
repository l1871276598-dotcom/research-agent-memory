> Released snapshot for repository auditability (Human Review Phase Gate 0 contract sync).
> Authoritative source lives in the local design vault; this copy is
> frozen as of 2026-07-13 and changes only through a new gate.

---
type: design-baseline
date: 2026-07-11
tags: [laos, human-review-phase, design, decision-event, state-machine]
status: approved-frozen (v1.8, r8 CONVERGED + E1 微修正, 人工批准 2026-07-11; E1 v1.7→v1.8 人工批准 2026-07-13)
source: "[[Human Review Phase 范围界定]]（r3.1，approved-frozen 2026-07-11，九 Gate 全裁定）"
related:
  - "[[Phase 5 Design Baseline v4.3 — Consolidated Reconstruction|基线 v4.3.2]]"
  - "[[Human Review Phase 第一性分析审查记录]]"
---

# Human Review Phase 设计基线 v1（草案）

> 控制文档：范围界定 r3.1（九 Gate）；基线 v4.3.2 §7.1（经本文件 §9 的 A5 修正案 supersede，A5 已于 2026-07-11 人工批准生效并入册基线 §14-A5）、§9（落盘语义）、附录 C（canonicalizer_version=1）。
> 口径：§1–§8 为**已批准冻结**设计（2026-07-11 人工批准，r8 CONVERGED）；§9 为 A5 修正案正式文本（**已生效**）；计划级实现决定标注 ⚙。G0（批准前不存在 decision 写入路径）已随批准解除——写入路径自实施阶段起按本文件语义实现。
> **v1.8 变更（E1 微修正，2026-07-13 人工批准，单项文本核销）**：§6 错误映射表新增 `io/clock_unavailable`（decide 构造事件对象时时钟不可用/不可表示，必填/必填，侧效应归 Rejected-before-commit），可返回 code 31→32（io 6→7）。仅此一行 + 数量同步，§1–§5/§7–§9 及其余 §6 条款原样不变。

## 1. Decision 事件 schema（冻结候选）

```json
{
  "schema_version": 1,
  "review_queue_item_id": "rq_<sha256>",
  "decision_seq": 1,
  "action": "accept",
  "operator_claim": "",
  "reason": "",
  "recorded_at": "2026-07-11T00:00:00Z",
  "decision_digest": "dcd_<sha256>"
}
```

- 全 8 键封闭集合；`action ∈ {accept, reject, defer, revise}`（展示顺序即此冻结中性顺序，无规范效力）；`decision_seq ∈ {1, 2}`；`operator_claim`/`reason` 为非空字符串（调用方自述，不验证——G3a）。
- `recorded_at`：词法冻结 `YYYY-MM-DDTHH:MM:SSZ`（大写 Z、无小数秒、无闰秒、真实日历校验，r2-m2）；writer 非权威墙钟观察（记录时点 = writer 构造事件对象时刻）；不参与排序/幂等/派生/授权（G7）；writer 时钟不可用或不可表示 → fail-closed 拒写；**时钟可表示但漂移/回拨 → 照记不拒**（r1-m11）。
- `decision_digest = "dcd_" + SHA-256(canonical(payload 除 decision_digest 外的 7 键))`——仅一致性/偶然损坏检测（G3b）；**threat model 明示（r1-m12）：有 OS 写权限者可同时改写 payload 与 digest，同进程同用户直接文件写入不设防（TCB 假设）**；canonical 规则 = 附录 C（canonicalizer_version=1，模块内私有实现，同 S5.4 先例 ⚙）。
- 文件字节 = canonical JSON 全 8 键（附录 C：sort_keys、紧凑分隔、ensure_ascii、无尾随换行）。

## 2. 存储布局与命名（冻结候选）

```
state_dir/
  review_queue/{rq_id}.json          （Phase 5 产物，本阶段只读）
  decisions/{rq_id}.decision_{seq}.json   （本阶段唯一可追加 namespace）
```

- `decisions/` 为独立顶层目录（G4 独立 namespace）；文件名沿用 §7.1 冻结模式 `{rq_id}.decision_{seq}.json`，目录归属为本设计新定（§7.1 未规定目录）。
- ID 语法：`^rq_[0-9a-f]{64}$` 前置强制；`seq` 仅接受字面 `1`/`2`；路径超限 fail-closed。

## 3. 状态机与归约（冻结候选）

- 状态机（§7.1 冻结原样保留）：`pending →(seq1: accept|reject|revise) 终态`；`pending →(seq1: defer)→ deferred →(seq2: accept|reject|revise) 终态`；seq2 禁 defer；deferred 为唯一允许 seq2 的前态。
- 派生视图：`ReviewState(rq) = Reduce(request, decisions normalized by (rq_id, seq))`——纯函数、与文件枚举顺序无关、重放一致；输入为**通过 §5 校验的** decision 集合。
- 非法输入（未知 action、seq∉{1,2}、seq2 无 seq1、seq1 已终态后的任何 seq2、seq2=defer）→ 归约 fail-closed 报错，不产生部分状态。

## 4. 写入流程（冻结候选）

顺序：① 只读路径 Gate（state_dir 存在性/symlink/同步根/forbidden_roots——继承 S5.4 ⚙-5/⚙-5a 全套）→ ② **G8 管辖权校验**（只读）→ ③ 前态判定（只读读取既有 decisions）→ ④ 幂等/冲突判定（G5）→ ⑤ 原子发布（G2）。

- **② 管辖权（G8，r1-M4 收紧）**：经**独立只读校验 seam `validate_frozen_review_chain`**（新函数；**不复用** persistence 的 _preflight/persist/resume——它们会清 temp、跑 probe、进入发布路径；本 seam 冻结为零 cleanup、零 probe、零 mkdir/link/unlink）执行：rq 文件 canonical 字节自检 + **从链重建预期 rq 并逐字节比对**（经冻结 build_review_queue_request）+ 文件名/内嵌 ID/`rq_` identity/schema_version/恒 pending 全校验 → locator 解析 digest 目录 → E/R/El 经冻结 producer 链重建逐字节相等 → 锚点等式。任一失败 → 零 decision、零回写、fail-closed。`evaluation_id` 链内真实性边界（G-1）原样继承。
- **②b Q→D 持久交接（r1-M3）**：decision 发布前，writer 对 `review_queue/` 补做一次幂等 `fsync`（使 Q 的目录项先于 D 持久——消除"掉电后 Q 丢 D 留"的窗口）；读取侧遇 `D ∧ ¬Q`（decision 存在而对应 rq 缺失）→ corruption，fail-closed 交人工，归约拒绝。
- **③ 前态**：枚举 decisions/ 中该 rq 的既有事件（decisions/ 为本阶段新建 namespace，不在基线 §7 queue 枚举禁令范围内）；既有事件先过 §5 完整性校验。**review_queue/ 的只读枚举权由 A5-8 显式授予 Human Review Subsystem**（r1 撤回"按已知文件名读取即可"的设计——无枚举权则 review.list 不可实现；§7 对 Phase 5 Producer 的禁令不变）。
- **④ 幂等/冲突（G5 收紧版 A）**：目标 `(rq_id, seq)` 已有事件 → 先过 §5 全套校验，再**补做一次幂等 `fsync(decisions/)`**（r1-M2：link 后目录 fsync 前的窗口内，另一方不得在未重建持久性前报告成功）→ 完整命令语义投影（rq_id/seq/action/operator_claim/reason，**精确 canonical/code-point 相等，禁止 trim/大小写/Unicode 归一化**，r1-m10）相同 → **无写入幂等返回该既有持久事件原样**（含首次写入的 recorded_at 与 decision_digest，禁止构造重试新对象，r1-M5）；任一字段异 → 确定冲突报错。目标槽空但前态不允许该 seq → fail-closed。§5 校验失败的既有文件 → durability-unknown/损坏，fail-closed 交人工，不得幂等返回。
- **⑤ 发布（G2 核心，r2-M1/M4 补全）**：decisions/ 目录 **bootstrap**（`mkdir` → EEXIST 时 `lstat` 验证为真实非 symlink 目录 → **无论谁创建，每个 writer 无条件补 `fsync(state_dir)`** 后方可进入 temp 阶段；双写者首次创建入验收矩阵）→ 同目录 temp 独占创建 → 写 canonical 字节 → fsync(temp) → temp readback（重读失败 → `io/temp_read_failed`；字节不符 → `corruption/temp_byte_mismatch`；temp 在 link 前消失 → `corruption/temp_missing_before_link`，writer 存活时自身 temp 消失只能是协议外干预）→ link(temp, final)（create-if-absent；已存在 → 回到④只读判定）→ unlink(temp) → fsync(decisions/) → **post-publish final readback：重读 final path，过 §5 全校验且 raw bytes == expected bytes，才允许返回发布成功**；失败 → durability_unknown/corruption，不得声称成功。禁 os.replace。
- **temp 所有权协议（r1-C1，decisions/ 为多写者 namespace，不得照搬 S5.4 单写者清理）**：每个 writer 仅创建并清理**自己的** temp（nonce 命名，成功或失败均在 finally 清理自身 temp）；**自有 temp 清理失败语义冻结（r4-M1-E）**：final 已发布且 post-publish readback 通过时，自身 temp unlink 失败**不阻止成功返回**（返回 published，残留属 `.tmp.*` 保留前缀，读取忽略，离线维护清除）；final 未成功时 temp 清理失败不改变原错误分类；**正常写入路径永不删除他人 temp**；崩溃残留 temp 仅由**显式离线维护操作**清除——不注册为 review agent handle、仅在全部 [[LAOS|LAOS]] writer 停止后由 operator 运行、系统**不声称**运行时验证"当前无 writer"（离线 operator 前置条件，r2-m3）。temp 为非 artifact（`.tmp.` 前缀），读取路径一律忽略。验收含"双写者之一暂停于 temp 阶段"场景：另一方必须得到确定的发布/幂等/冲突三态之一，不得出现 ENOENT 类随机错误。
- 残缺/digest 不符/非 canonical 的既有 final → 零覆盖 fail-closed 交人工（占位死锁的承诺仅限合规崩溃路径）。

## 5. 读取校验（冻结候选）

任何读取 decision 的路径（前态判定、归约、幂等比较）必须全过（r1-M7 补全）：canonical 字节自检（raw == canonical(parse(raw))）→ 8 键封闭 → **完整 schema/值域校验**（schema_version==1、action∈四枚举、seq∈{1,2}、operator_claim/reason 非空字符串、recorded_at 满足 §1 冻结精确词法 `YYYY-MM-DDTHH:MM:SSZ`（大写 Z、无小数秒、无闰秒、真实日历校验）、各字段类型精确）→ digest 复算相符 → 文件名与内嵌 (rq_id, seq) 绑定相符。任一失败 → 该事件视为损坏，fail-closed（不得进入归约、幂等比较或返回）。

## 5b. 持久观察屏障与并发采集（冻结候选，r2-M2/M3）

- **Durable Observation Barrier**：任一接口（review.list / review.show / 依赖既有 decision 的前态拒绝）在依据已存在的 Q/D 返回派生状态、事件或拒绝结果前，必须先对**已确认存在且为非 symlink 真实目录**的 namespace 完成 `fsync(review_queue/)` 与 `fsync(decisions/)`——**合法缺失的空 namespace 不执行 fsync**（r7-M1，缺失语义见下条）——link 后未 fsync 的可见 final 不得被呈现为 committed 裁决。验证 seam 保持纯只读，屏障位于 handler/store 层。
- **单 rq 事件采集顺序冻结**：**先读 seq2，再读 seq1**（见到 seq2 时 seq1 必已先存在；未见 seq2 后再见 seq1 可线性化到检查 seq2 的时刻）——消除"reader 混合两个时刻误报 seq2-without-seq1"的交错。
- **全局 list 采集算法（r3-M1 冻结为有界两轮，关系校验制）**：
  ```text
  Pass 1: D1 = 采集并逐一 §5 校验 decisions/；Q1 = 采集并校验 review_queue/
  若 D1 中每个 decision 均有 Q1 中对应 request（外键关系校验，非集合相等）→ 用 (D1, Q1) 归约返回
  否则：执行 Durable Observation Barrier → Pass 2 重采集 (D2, Q2)
  若 D2 外键关系成立 → 用 (D2, Q2) 归约返回
  否则 → corruption: orphan_decision
  ```
  **不要求两轮集合相等**——合法新 Q/D 可在线性化点之后出现（新 Q 仅表现为 pending 项，不制造假 orphan）；最多两轮、禁止无限重试、禁止返回部分结果。
- **Namespace 缺失与终点类型语义（r7-M1 冻结）**：`review_queue/` 不存在 → **合法空 namespace**（Phase 5 仅在首个 eligible request 发布时创建该路径，state_dir 初始化不预建）：review.list 取 `Q = ∅`、不对其执行 fsync、（decisions/ 亦无 orphan 时）返回空列表；review.show / review.decide 在 rq_id 合法解析后遇 queue namespace 缺失**或**目标 rq 文件缺失 → 统一 `jurisdiction/request_not_found`。`decisions/` 不存在 → 合法空 namespace（`D = ∅`，不执行 fsync）。namespace **存在但非"非 symlink 真实目录"** → 各自唯一分类：review_queue/ → `corruption/malformed_review_queue_namespace`（新增 code）；decisions/ → `corruption/malformed_decisions_namespace`——类型非法**不得伪装为"未找到"**。
- **目录条目语言（fail-closed，r3-m2 修正）**：`.tmp.*` 为 Human Review **保留非 artifact 前缀**——读取路径对该前缀条目一律不解析、不归约、不返回（**忽略按前缀判断**）；**删除按 writer 所有权判断**（temp 所有权协议）——reader 不验证不可验证的"自产"属性。除保留前缀与合法 artifact 名（review_queue/ = `rq_<64hex>.json`；decisions/ = `{rq_id}.decision_{1|2}.json`）之外的任何条目、任何 symlink/目录/未知文件类型 → corruption fail-closed，**不得静默跳过**。
- **Q–D 全局对账**：list 必须同时核对两个 namespace——孤儿 D（`D ∧ ¬Q`）经 decisions/ 采集必然可达并按 corruption 呈报，不允许"从 Q 出发永不可达"。

## 6. 能力矩阵与模块边界（冻结候选）

- 矩阵 = 范围 G4 的 3×3 逐格定义原样冻结；实现为独立模块 `src/human_review/`（⚙：decision.py 纯函数层 + review_store.py I/O 层，I/O 全经单一受审 shim，同 S5.4 纪律），与 `src/learning_loop/`（Producer）无相互 import 写路径。
- audit 测试：Producer 触 decisions/ 必败；human_review 写 chain/request/memory/policy/promotion 必败；**侧效应按两阶段验收（r6-M1，r7-m1 口径）**——pre-link rejection 断言**本调用零新增**正式 decision（不断言 namespace 为空）；post-link uncertain 断言不虚报 published、不回滚，可经重试/show 确定状态。
- CLI：注册第 13 个 agent `human_review_agent`（handles: review.list / review.show / review.decide），仅人类命令传输层，无自动裁决路径。**路由约束（r1-M8）**：review.* 冻结为 **context-free 路由**（加入 orchestrator 的 context-free 白名单）且组装**惰性/独立**——review 路径零 MemoryStore 加载、零 active_relevant 注入（验收断言全路径无 memory 读取）；若现有 build_application 急切构造 MemoryStore，则 review 走独立组装入口。
- **接口 schema 冻结（r1-M9、r2-M5 精确化）**：
  - review.list：输入 `{}`；响应 `{"items": [{"rq_id": "...", "state": "pending|deferred"}]}`，按 rq_id **code-point 升序**（冻结中性顺序，无优先级含义，不依赖文件系统枚举序）；`review_queue/` 合法缺失 → `{"items": []}`（**非错误**，r7-M1）。
  - review.show：输入 `{"rq_id": "..."}`；响应 `{"request": <经 G8 校验的完整 12 键 review_queue_request 对象原样（封闭 12 键，不增删字段，不加呈现字段，不从 summary 反解析，alias-free）>, "events": [<decision 8 键原样>...按 seq 升序], "state": "pending|deferred|accepted|rejected|revised"}`。
  - review.decide：输入 `{"rq_id", "decision_seq"(必填，不推导), "action", "operator_claim", "reason"}` 封闭集；**成功响应仅二态** `{"status": "published"|"idempotent", "event": <既有或新发布事件 8 键原样>}`；**冲突是错误不是成功态**：`{"error": {"category": "conflict", "code": "decision_slot_conflict", "rq_id": "...", "decision_seq": N}}`。
  - **错误 envelope 封闭（r3-M2、r4-M1 补全）**：`{"error": {"category", "code", "rq_id"?, "decision_seq"?}}`——四键封闭，无 message 自由文本，不暴露原始 I/O 异常，unknown code 不允许。**上下文键的机器可判规则（冻结）**：`rq_id` 当且仅当错误发生于"已成功解析且通过语法校验的 rq-scoped 操作"内时必填，否则**不得出现**；`decision_seq` 当且仅当错误发生于"已成功解析为整数 1/2 的 slot-scoped 操作"内时必填，否则**不得出现**（判据 = 解析状态，非错误类型）。
  - **规范性错误映射表（32 个可返回 code；另有 1 个 reserved identifier，r6-m1/r7-M1/E1）**：
    | category | code | 触发路径 | rq_id / seq（按上规则的固定结果） |
    |---|---|---|---|
    | jurisdiction | invalid_review_chain | G8 链校验任一环失败 | 必填 / 视 slot 解析 |
    | jurisdiction | request_not_found | rq 语法合法但 review_queue/ 合法缺失或目标 rq 文件不存在（r7-M1） | 必填 / 视 slot 解析 |
    | precondition | invalid_request | task/input 容器不是对象，或出现未知键（**收窄定义**：字段缺失、类型、值域错误一律由字段专用 code 处理） | 视解析 / 视解析 |
    | precondition | invalid_review_queue_item_id | rq_id 缺失、类型或正则非法 | 禁止 / 禁止 |
    | precondition | invalid_decision_seq | seq 非整数 1/2（含 bool/字符串） | 必填 / 禁止 |
    | precondition | invalid_action | action 非四枚举或非字符串 | 必填 / 必填 |
    | precondition | invalid_operator_claim | 空或非字符串 | 必填 / 必填 |
    | precondition | invalid_reason | 空或非字符串 | 必填 / 必填 |
    | precondition | invalid_state_transition | 前态不允许该 seq（含终态后写入、seq2 无 defer 前态） | 必填 / 必填 |
    | precondition | invalid_state_dir | state_dir 不存在/非目录/symlink/同步根/forbidden root | 禁止 / 禁止 |
    | precondition | unsafe_path | 路径组件 symlink/类型异常 | 视解析 / 视解析 |
    | precondition | path_too_long | 路径超限 | 视解析 / 视解析 |
    | conflict | decision_slot_conflict | 同槽既有事件语义投影不同 | 必填 / 必填 |
    | corruption | malformed_directory_entry | 枚举遇非法条目/类型 | 视解析 / 禁止 |
    | corruption | malformed_decisions_namespace | decisions/ 本身为文件/symlink | 禁止 / 禁止 |
    | corruption | malformed_review_queue_namespace | review_queue/ 存在但非"非 symlink 真实目录"（r7-M1） | 视解析（list 全局检查 → 禁止；show/decide 已解析 rq → 必填） / 视解析 |
    | corruption | malformed_request | rq 文件非 canonical/schema 非法/重建不符 | 必填 / 视 slot 解析 |
    | corruption | malformed_decision | decision 文件 §5 校验失败 | 必填 / 必填 |
    | corruption | orphan_decision | 两轮采集后 D∧¬Q | 必填 / 禁止 |
    | corruption | temp_byte_mismatch | temp readback 字节不符 | 必填 / 必填 |
    | corruption | temp_missing_before_link | 自身 temp link 前消失 | 必填 / 必填 |
    | corruption | decision_final_byte_mismatch | post-publish 字节不符 | 必填 / 必填 |
    | corruption | decision_final_missing | link+fsync 后 final 消失 | 必填 / 必填 |
    | durability_unknown | decision_directory_fsync_failed | final link **后**的 fsync(decisions/) 失败 | 必填 / 必填 |
    | io | precommit_directory_fsync_failed | final link **前**的任一目录 fsync 失败（state_dir bootstrap / review_queue 交接 / 观察屏障） | 视解析 / 视解析 |
    | durability_unknown | final_readback_failed | post-publish 重读系统调用失败（fsync 已成功） | 必填 / 必填 |
    | io | path_read_failed | 只读采集/读取系统调用失败 | 视解析 / 视解析 |
    | io | temp_write_failed | temp 创建/写入失败 | 必填 / 必填 |
    | io | temp_read_failed | temp readback 系统调用失败 | 必填 / 必填 |
    | io | link_failed | link 非 EEXIST 失败 | 必填 / 必填 |
    | io | mkdir_failed | decisions/ mkdir 非 EEXIST 失败或 lstat 失败 | 禁止 / 禁止 |
    | io | clock_unavailable | decide 构造事件对象时 writer 时钟不可用或不可表示（E1，仿 A4 先例，2026-07-13 人工批准 v1.7→v1.8） | 必填 / 必填 |
    （"视解析" = 按上方机器可判规则由解析状态定，两种结果都合法且唯一确定。）**可返回 code 共 32 个**（jurisdiction 2 / precondition 10 / conflict 1 / corruption 10 / durability_unknown 2 / io 7，其中 directory fsync 按 commit 前后拆为两 code（r6-M2-B），queue namespace 终点独立分类（r7-M1），`clock_unavailable` 由 E1 微修正加入（io 6→7，31→32，2026-07-13 人工批准））；`precondition/concurrent_change` 移入 **reserved identifiers**（非当前可返回 code，运行时响应中出现即违规，未来启用须 bump schema，r5-m1）。
  - **唯一分类顺序（r5-M1 冻结，消除触发重叠）**：
    - **输入校验序**：① 顶层非对象 → invalid_request；② 未知键 → invalid_request；③ 缺失字段 → 各字段专用 code（rq_id→invalid_review_queue_item_id、seq→invalid_decision_seq、action→invalid_action、operator_claim→invalid_operator_claim、reason→invalid_reason）；④ 字段类型/值域 → 同上字段专用 code。
    - **文件系统错误归属**：bootstrap 阶段对 decisions/ 的 mkdir/lstat 失败 → mkdir_failed；进入读取/采集阶段后的 read/lstat 失败 → path_read_failed。
    - **path/namespace/entry 互斥优先级（r6-M2-A，r7-M1 扩为五级）**：① state_dir 根对象本身非法（不存在/非目录/symlink/同步根/forbidden root）→ invalid_state_dir；② state_dir 到目标 namespace 之间的**祖先路径组件**非法 → unsafe_path；③ review_queue/ namespace **终点自身**存在但非"非 symlink 真实目录" → malformed_review_queue_namespace；④ decisions/ namespace **终点自身**为文件/symlink → malformed_decisions_namespace；⑤ 两 namespace 内**被枚举的子条目**类型非法 → malformed_directory_entry。unsafe_path **不覆盖**根、两 namespace 终点与 terminal 子条目——五类互斥。namespace 终点**合法缺失非错误**（语义见 §5b）。
    - **post-publish final 校验序**：final 路径不存在 → decision_final_missing；read 系统调用失败 → final_readback_failed；raw bytes ≠ expected → decision_final_byte_mismatch；字节相等后 §5 失败 → malformed_decision。每个实际失败唯一命中一个 code。
  - **post-publish 失败分类表（r3-M2 消除二义；顺序见上方唯一分类序）**：final 路径消失 → `corruption/decision_final_missing`；read 系统调用失败（fsync 已成功）→ `durability_unknown/final_readback_failed`；raw bytes ≠ expected → `corruption/decision_final_byte_mismatch`；字节相等后 §5 失败 → `corruption/malformed_decision`；final link 后的 fsync(decisions/) 失败 → `durability_unknown/decision_directory_fsync_failed`；link 前任何目录 fsync 失败 → `io/precommit_directory_fsync_failed`（本调用零新增 decision）。
  - **侧效应两阶段语义（r5-M2，替换"全部被拒路径零侧效应"）**：
    - **Rejected-before-commit**（jurisdiction / precondition / conflict / temp 阶段 corruption 与 io / link 之前一切失败）：保证**本调用不新增正式 decision**；**不对调用前已存在或并发产生的合法 decision 作不存在承诺**（r7-m1）。
    - **Outcome-uncertain-after-publish-attempt**（decision_directory_fsync_failed、final_readback_failed 及全部 post-publish corruption/missing）：final **可能已可见或已持久**——不得声明 published、不得声明零事件、**不得覆盖或回滚既有槽位**（append-only 优先）；调用方经同命令重试（可得 idempotent）或 review.show 重新确定状态。

## 7. 零自动效力声明（G1-B′，冻结候选）

本阶段交付的一切读取接口（列表/详情/派生状态）均为**呈现用途**；不存在任何依据 decision 内容改变系统行为的代码路径。correction 机制挂起；**重开条件 = 首个自动依赖 decision 的消费者立项**（届时必须重开 G1 并走新修正案）。

## 8. 验收框架

范围 r3.1 §6 七条判据原样继承为验收基线；另加：A5 生效前 decision 写入路径不存在的验证（feature gate 或模块不可达性证明 ⚙）。

## 9. 修正案 A5 正式提案文本（对基线 v4.3.1 §7.1 的修订；批准即生效并 bump v4.3.2）

1. **字段更名与语义**：`operator` → `operator_claim`（调用方自述，系统不验证现实身份，授权为接口前置条件）；`decided_at` → `recorded_at`（writer 非权威墙钟观察，词法 = A5-3 冻结的 `YYYY-MM-DDTHH:MM:SSZ`，禁止参与排序/幂等/派生/授权；时钟不可用 → 拒写）。
2. **幂等规则替换**：原"写 seq N 时若同名文件已存在——action 相同 → 幂等返回既有 decision（operator/reason 以先写者为准）；action 不同 → 拒绝"**废止**，替换为：完整命令语义投影（rq_id, seq, action, operator_claim, reason；精确 canonical/code-point 相等，禁止任何归一化）严格相同 → 无写入幂等返回**经校验的既有持久事件原样（含首次写入的 recorded_at 与 decision_digest，禁止构造重试新对象）**；任一字段不同 → 确定冲突拒绝。"发布后响应丢失的同命令重试 = 无写入幂等返回"与"终态后禁新 decision"为两条独立规则。
3. **schema 增补（完整规范性定义随本条冻结）**：decision 事件为本文件 §1 的 8 键封闭 schema；`schema_version` 恒为整数 1；`decision_digest = "dcd_" + SHA-256(canonical(其余 7 键))`，canonical 规则 = 基线附录 C（canonicalizer_version=1），digest 自排除；digest 仅一致性/偶然损坏检测，无防篡改承诺；`action ∈ {accept, reject, defer, revise}`、`decision_seq ∈ {1,2}`、`operator_claim`/`reason` 非空字符串、`recorded_at` 词法冻结为 `YYYY-MM-DDTHH:MM:SSZ`（大写 Z、无小数秒、无闰秒、真实日历校验）。
4. **存储归属**：decision 事件存于 `state_dir/decisions/`（独立 namespace）；文件名模式 `{rq_id}.decision_{seq}.json` 不变。
5. **管辖权前置**：任何 decision 写入前必须通过管辖权校验（范围 G8）；不可绑定合法冻结链 → 零 decision。
6. **效力边界**：decision 仅承载 evaluation disposition 的证据准入语义（范围 G6 表）；不产生任何系统控制的自动下游治理效力（范围 G1-B′）；correction 挂起及其强制重开条件随本修正案入册。
7. **其余 §7.1 语义**（状态机、seq∈{1,2}、defer 仅 seq1、create-if-absent 仲裁、append-only、request 永不改写）**原样保留**。
8. **枚举授权（对基线 §7 的限定例外，与 A4 同型）**：Human Review Subsystem 获得 `review_queue/` 的**只读枚举**权限，仅用于呈现 pending/deferred 请求（review.list）与管辖权校验；不得据枚举结果执行任何写入或治理动作；Phase 5 Producer 的 §7 禁令原样不变。
9. **Q→D 持久交接与孤儿处理**：decision 发布前 writer 补做幂等 fsync(review_queue/)；`D ∧ ¬Q` 为 corruption，fail-closed 交人工。
10. **持久观察与并发采集**：可见 final 在其目录 fsync 前不得被任何读取接口视为 committed；list/show/依赖前态的拒绝在返回前执行持久观察屏障（仅 fsync **已存在且为非 symlink 真实目录**的 namespace；合法缺失的 namespace 视为空集合、不执行 fsync——review_queue/ 缺失时 list 返回空集，show/decide 归 request_not_found，r7-M1）；单 rq 事件采集顺序冻结（先 seq2 后 seq1）；全局采集冻结为有界两轮关系校验算法（外键校验非集合相等；两轮后仍有 orphan → corruption；禁止无限重试与部分结果）；归约的顺序无关性不替代文件系统快照规则。
11. **Decision namespace bootstrap 与完整性**：decisions/ 并发 mkdir-or-verify（EEXIST → lstat 验证真实非 symlink 目录）；每个 writer 无条件补 fsync(state_dir)；发布成功以 post-publish final readback（§5 全校验 + 字节相等）为准；双 namespace 对账（孤儿 D 必达并呈报）；未知目录项、非法文件类型、orphan D 一律 fail-closed。
