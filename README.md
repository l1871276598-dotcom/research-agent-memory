# Local Agent Operating System (LAOS)

本仓库是 **Local Agent Operating System（LAOS）** 的本地优先实现。它的目标不是只“记住”信息，而是把知识、项目、经验、规则和上下文统一到一个可持续演化的本地系统中，让 GPT、Codex、Claude、本地模型以及未来 Agent 能持续学习并复用过去成果。

当前代码仍处于 **Memory Core 阶段**：已经具备本地结构化记忆、SQLite FTS5 检索、ChatGPT ZIP 手动导入、Trusted Memory Loop、人工审核闸门和 Context Pack 输出能力。完整 LAOS 的 Orchestrator Agent、Agent Registry、Link Understanding Agent、Rule / Reflection / Evolution Agent 等仍属于后续路线图。

真实记忆、真实 ChatGPT ZIP、真实聊天记录、未发表资料、PDF、SQLite、日志、缓存、API key、密码、SSH 私钥和 token 不得提交到 GitHub。GitHub 只保存代码、Schema、模板、测试和文档；Markdown 记忆、raw 原始证据和稳定文本副本保存在用户选择的数据目录；活动 SQLite、WAL/SHM、缓存和日志必须保存在本地 state 目录。

## 核心定位

LAOS 的最高目标是构建 **Agent 的本地学习层**：

```text
输入资料 / 任务结果
→ 结构化沉淀
→ 候选记忆
→ 质量检查
→ 人工审核
→ 长期记忆更新
→ Context Pack 编译
→ Agent 调用复用
→ 任务结果反哺
```

它不是普通 memory database，也不是单一 Agent 框架，而是一个以本地记忆为核心资源、以可插拔 Agent 为能力单元、以审核机制为安全边界的本地 Agent 操作系统。

## 最高架构公约

1. **Memory Core 是唯一可信知识源**：长期知识、项目状态、经验和规则必须通过标准接口进入 Memory Core。
2. **所有长期记忆更新必须经过 Review Gate**：任何 Agent 都不能绕过人工审核直接写入 active memory。
3. **所有能力优先以 Agent 形式存在**：新增需求优先新增或替换 Agent，而不是重构核心系统。
4. **Orchestrator 只调度，不承载业务逻辑**：总控 Agent 负责理解需求、选择 Agent、组装 Context Pack 和收集结果。
5. **Context Pack 必须最小化**：按任务、项目、可信度、时间有效性和保密等级筛选，不全量灌入上下文。
6. **restricted 内容永不导出**：搜索、Context Pack 和外部 Agent 调用默认排除 restricted。
7. **外部系统通过 Adapter 接入**：文献、网页、GitHub、Gmail、Zotero/EndNote 等不内置进 Memory Core。

## 目标架构

```text
User
  │
  ▼
Orchestrator Agent
  │
  ▼
Agent Registry
  │
  ├── Import Agent
  ├── Memory Agent
  ├── Project Agent
  ├── Rule Agent
  ├── Reflection Agent
  ├── Context Agent
  ├── Search Agent
  ├── Review Agent
  ├── Quality Agent
  ├── Link Understanding Agent
  ├── Code / Codex Bridge Agent
  ├── External Adapter Agent
  └── Evolution Agent
```

### Orchestrator Agent

唯一总控和 Router。负责理解需求、判断任务类型、读取相关记忆、选择合适 Agent / Skill、组装最小 Context Pack、收集执行结果，并把结果转成候选记忆或反思候选。

### Agent Registry

所有 Agent 的注册中心。未来新增需求时，优先注册一个新 Agent，而不是修改 Orchestrator 或重构核心。

### Memory Agent

维护长期记忆生命周期：

```text
raw evidence
→ candidate
→ active knowledge / experience / rule / project state
→ deprecated / conflict / archived
```

### Import Agent

负责导入 ChatGPT ZIP、手动文件、本地文件夹和未来外部来源。导入只生成 raw evidence 或候选，不直接激活长期记忆。

### Link Understanding Agent

负责把 GitHub、网页、帖子、论文、PDF、YouTube、Notion、Google Docs 等链接转换为任何模型都能读取的标准 Markdown / JSON 上下文。Hermes、Browser、Firecrawl、Jina Reader、Playwright 等都只是它的 provider。

### Project Agent

维护每个项目的持续状态、阶段、TODO、风险、决策和下一步，使项目成为可持续演化对象。

### Rule Agent

从成功、失败、修正和反思中提炼可复用规则。规则必须先成为 rule candidate，再经 Review Gate 审核后进入长期记忆。

### Reflection Agent

从任务执行结果中生成 reflection candidate，例如失败原因、复用经验、下次避免方式和可晋升规则。

### Context Agent

根据任务动态编译最小 Context Pack，供 GPT、Codex、Claude、本地模型或其他 Agent 使用。

### Search Agent

隐藏底层检索实现。当前为 SQLite FTS5 lexical search，未来可扩展 tag、knowledge graph、embedding 和 hybrid search。

### Review Agent

所有长期记忆更新的安全闸门。负责 candidate 的 accept、reject、merge、support、supersede、conflict 等审核动作。

### Quality Agent

负责去重、冲突检测、来源校验、敏感性继承、可信度检查和状态流转检查。

### Code / Codex Bridge Agent

未来统一连接 Codex、Claude Code、Cursor、OpenHands 等代码执行或审查工具。

### External Adapter Agent

外部工具接入层。文献系统不作为内置主线，只保留 external adapter，例如未来需要时接 Zotero、EndNote、本地论文库或其他资料源。

### Evolution Agent

观察整个 LAOS 的使用情况，发现重复工作、低质量规则、无人调用的 Agent、架构漂移和可优化点，只提出 evolution candidate，不直接修改系统。

## 当前版本状态

- 当前软件版本：v0.7.0 Memory Core
- SQLite schema：v3
- 默认检索模式：lexical
- 主要支持平台：macOS
- 支持的 Python：3.11 或更高版本
- 当前不需要 Codex CLI、语义模型、云 API 或付费 API

## 当前已实现能力

- 初始化本地数据目录
- 添加、验证和导出结构化 Markdown 记忆
- `profile`、`context`、`principle`、`project`、`decision`、`procedure`、`session` 和 `context_transition`
- workspace、project、confidentiality 和时间有效性过滤
- SQLite FTS5 schema v3 派生索引
- 结构化记忆与文档的统一 lexical 检索
- ChatGPT 官方 ZIP 本地手动导入
- 手动文件 raw 归档和稳定 text sidecar
- 文档元数据覆盖
- Trusted Memory Loop：确定性 `ADD` / `UPDATE` / `DEPRECATE` / `NOOP` / `REVIEW_REQUIRED`，写前校验、人工闸门、自动重索引、写后验证和 durable resume journal
- Agent Context Pack JSON/Markdown 输出
- lexical 检索评测框架
- `semantic` / `hybrid` 模式接口的显式 lexical 回退
- `doctor` 健康检查
- GitHub Actions 在 push / pull_request 运行测试、compileall 和 whitespace 检查

## 尚未实现能力

- Agent Registry
- Orchestrator Agent / Router
- 可插拔 Agent 目录结构
- 自动更新 Loop：watch/import → detect → candidate → quality gate → review → apply → reindex → refresh context pack
- 新导入对话自动批量生成候选记忆
- Link Understanding Agent / Hermes provider
- Rule Agent
- Reflection Agent
- Evolution Agent
- Code / Codex Bridge Agent
- owner/agent 身份认证与多 Agent 授权
- 真实本地向量 embedding
- 真实语义相似度检索
- 真实 lexical + semantic 混合排序
- MCP 接口

候选审核生命周期已经实现；自动候选生成调度尚未实现。

## 不作为内置主线的能力

以下能力不进入 Memory Core 主线，未来需要时通过 External Adapter Agent 接入：

- 文献矩阵自动填充
- Zotero / EndNote 同步
- 内置 PDF/DOCX 深度文献系统
- GUI
- 大型分布式多 Agent 自主协商系统
- 云数据库
- 常驻后台监听服务

## 当前数据目录布局

`init` 当前仍会创建以下数据目录和文件：

```text
ResearchAgent/
├── memory/
│   ├── profile/
│   ├── contexts/
│   ├── transitions/
│   ├── principles/
│   ├── projects/
│   ├── decisions/
│   ├── procedures/
│   └── sessions/
├── imports/
│   ├── chatgpt/
│   │   └── conversations/
│   ├── manual/
│   │   ├── raw/
│   │   └── text/
│   └── document_metadata.json
├── literature/
│   ├── inbox/
│   ├── pdf/
│   ├── notes/
│   ├── journals/
│   └── literature_matrix.csv
├── manuscripts/
│   ├── current/
│   ├── evidence/
│   └── archive/
├── exports/
│   ├── database_snapshots/
│   ├── import_reports/
│   └── index_manifest.json
└── backups/
```

说明：`literature/` 是历史残留目录，后续不再作为内置文献系统主线。未来应迁移为 External Adapter 输出目录或在下一版本中移除/降级。

本地 state 目录至少包含：

```text
~/Library/Application Support/ResearchAgent/
└── memory.sqlite
```

SQLite、WAL、SHM、锁、缓存和日志都属于可重建的本地派生状态，不应放在 iCloud 数据目录。

## 快速开始

```bash
REPO_ROOT="/Users/user/projects/research-agent-memory"
DATA_ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent"
STATE_DIR="$HOME/Library/Application Support/ResearchAgent"
cd "$REPO_ROOT"
```

```bash
python3 src/memory.py init --root "$DATA_ROOT"
```

```bash
python3 src/memory.py db-init \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

```bash
python3 src/memory.py add \
  --root "$DATA_ROOT" \
  --type principle \
  --title "代码最少原则" \
  --scope global \
  --workspace personal \
  --confidentiality personal \
  --source user \
  --confidence confirmed \
  --content "使用尽可能少的代码实现相同功能。" \
  --tags coding architecture
```

```bash
python3 src/memory.py validate --root "$DATA_ROOT"
python3 src/memory.py index --root "$DATA_ROOT" --state-dir "$STATE_DIR"
python3 src/memory.py search "代码最少" --root "$DATA_ROOT" --state-dir "$STATE_DIR"
python3 src/memory.py doctor --root "$DATA_ROOT" --state-dir "$STATE_DIR"
```

## 添加结构化记忆

```bash
python3 src/memory.py add \
  --root "$DATA_ROOT" \
  --type project \
  --title "PDC 项目" \
  --scope project \
  --project pdc \
  --workspace personal \
  --confidentiality personal \
  --source user \
  --confidence confirmed \
  --content "PDC project memory registry."
```

项目作用域的 `decision`、`procedure` 或其他记忆必须引用已经 review/accept 的 active `type: project` 记录。

所有创建类命令和函数只生成 `candidate`；`active` 只能由统一的 `memory_distill.py review/accept` 路径产生。兼容参数 `--confirmed` 仅记录 `confirmation: explicit` metadata，不会直接激活记录，也不会改写调用者提供的 `source`。当前版本不验证 review/accept 操作者的真实身份；owner/agent 身份认证仍未实现。

## 统一索引

```bash
python3 src/memory.py index \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --dry-run
```

```bash
python3 src/memory.py index \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

如需重建派生 SQLite：

```bash
python3 src/memory.py db-rebuild \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

Markdown 记忆、raw 原始证据和 text 稳定文本副本是权威数据。SQLite 是派生索引；`db-rebuild` 从权威文件重建临时数据库，验证通过后原子替换，失败时旧数据库保留。

## 搜索

默认搜索只返回 `active`、非 `restricted` 且符合项目边界的结果。未传 `--project` 时只返回 project 为空的记录；传入项目时只返回该项目和 project 为空的共享记录。只有显式 `--include-restricted` 或 `--include-inactive` 才放宽对应限制。`memory_tools.py search` 委托给 `memory.py search_store()`，没有独立 tokenizer 或过滤规则。

```bash
python3 src/memory.py search "论文证据链" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --kind document \
  --source-kind manuscript \
  --project pdc
```

```bash
python3 src/memory.py search "RMRE" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --json
```

当前索引源：

- `imports/chatgpt/conversations/`
- `imports/manual/text/`
- `imports/manual/raw/`，仅当没有对应 text sidecar 且 raw 是 UTF-8 可读文本
- `literature/notes/`，历史残留，后续应迁移为 external adapter 输出
- `literature/journals/`，历史残留，后续应迁移为 external adapter 输出
- `manuscripts/current/`
- `manuscripts/evidence/`
- `manuscripts/archive/`

手动导入资料优先索引 `imports/manual/text/`。同一份 TXT/JSON/CSV 等文件有 text sidecar 时，raw 不进入 FTS，避免重复结果。PDF/DOCX 等二进制 raw 不直接进入 FTS。

## ChatGPT ZIP 手动导入

当前唯一入口是：

```text
用户手动申请官方数据导出
→ 用户自行下载 ZIP
→ 本地运行 import-chatgpt
```

```bash
python3 src/memory_tools.py import-chatgpt \
  --zip "$HOME/Downloads/chatgpt-export.zip" \
  --root "$DATA_ROOT" \
  --dry-run
```

```bash
python3 src/memory_tools.py import-chatgpt \
  --zip "$HOME/Downloads/chatgpt-export.zip" \
  --root "$DATA_ROOT"
```

导入输出：

```text
imports/chatgpt/conversations/YYYY/MM/*.md
imports/chatgpt/import_manifest.json
exports/import_reports/*-chatgpt-*.json
```

导入会检查 ZIP 是否存在、是否为符号链接、ZIP CRC、唯一 `conversations.json`、JSON 根节点是否为 list、conversation 基本结构，并报告 `conversation_count`、`message_count`、`new`、`updated`、`unchanged`、`raw_only` 和 `failed`。报告不包含完整聊天正文。raw 使用独占创建；同路径同内容为 NOOP，同路径不同内容生成新的 hash 版本路径，既有文件不会被覆盖。重复导入旧 ZIP 不会重建 recent，也不会把旧内容晋升为长期记忆。

## 手动文件导入

```bash
python3 src/memory_tools.py import-manual \
  --path "$HOME/Downloads/note.txt" \
  --root "$DATA_ROOT" \
  --dry-run
```

```bash
python3 src/memory_tools.py import-manual \
  --path "$HOME/Downloads/note.txt" \
  --root "$DATA_ROOT"
```

支持的单文件入口参数是 `--path`。当前不支持目录扫描、inbox 扫描、`--file` 或 `--scan-inbox`。

文本类文件会写入：

```text
imports/manual/raw/YYYY/MM/*
imports/manual/text/YYYY/MM/*.md
exports/import_reports/*-manual-*.json
```

text sidecar front matter 包含 `source_path`、`source_sha256`、`original_name`、`media_type`、`extractor` 和 `imported_at`，可追溯到 raw 原始证据。manual raw 同样使用独占创建和 hash 版本路径，永不覆盖既有 raw；重复导入同一文件会报告 duplicate。导入不会自动运行 `index`。

PDF/DOCX 或无法 UTF-8 解码的文件只写 raw 和导入报告，报告 `archived_without_text: 1`，不写 text sidecar，不进入全文索引。

## Memory Agent 编排接口

`memory_agent.py` 是现有检索、路径安全和候选审核能力之上的薄编排层。`prepare` 在任务执行前返回有字符上限的相关上下文；`finalize` 在任务完成后生成待审核候选，不调用外部模型，也不会自动 accept 或 apply。

```bash
python3 src/memory_agent.py prepare \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task "整理 PDC 项目的证据链" \
  --project pdc \
  --max-chars 8000
```

```bash
python3 src/memory_agent.py finalize \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task "整理 PDC 项目的证据链" \
  --result-file completed-result.txt
```

`finalize` 成功输出包含 `operation: "finalize"`、真实 `candidate_count`、真实候选 `artifacts`、`review_required: true`、`applied: false` 和 `warnings`。第一版只进入现有候选审核队列，候选必须人工审核，绝不自动应用到权威记忆。

## 候选审核

候选审核只有一套当前实现的流程：

```text
apply
→ review
→ accept / reject
```

```bash
python3 src/memory_distill.py apply \
  --root "$DATA_ROOT" \
  --action create \
  --type principle \
  --title "候选原则" \
  --scope global \
  --workspace personal \
  --confidentiality personal \
  --source codex \
  --content "候选内容"
```

```bash
python3 src/memory_distill.py review --root "$DATA_ROOT" --json
```

```bash
python3 src/memory_distill.py accept --root "$DATA_ROOT" --id CANDIDATE_ID
```

```bash
python3 src/memory_distill.py reject \
  --root "$DATA_ROOT" \
  --id CANDIDATE_ID \
  --reason "证据不足"
```

记忆文件状态与审核状态分离：

- 记忆 `status`：`candidate`、`active`、`conflict`、`historical`、`archived`、`deprecated`
- 审核 `audit_status`：`prepared`、`awaiting_review`、`accepted`、`rejected`、`conflict`、`pending_delete`、`deleted`、`stale`、`failed`

行为边界：

- `create`：候选通过后变为 `status: active`、`audit_status: accepted`
- `merge`：目标必须存在；只合并 `source_refs`、`tags`、`relations`；候选归档为 `audit_status: accepted`
- `support`：不改目标核心 `content`；只增加来源和证据；候选归档为 `audit_status: accepted`
- `supersede`：accept 后新记录 active；旧记录 deprecated；维护双向 supersession 关系
- `conflict`：不覆盖正式记忆；候选变为 `status: conflict`、`audit_status: conflict`
- `reject`：候选归档为 `status: archived`、`audit_status: rejected`，保存 `review_reason`

如果候选记录带有 `source_path` 和 `source_sha256`，accept 前会重新校验 source hash。UPDATE/DEPRECATE proposal 同时保存目标 ID、预期状态和预期文件 SHA256；accept 会基于当前 store 重新校验并运行完整 hypothetical validation，变化时返回结构化 `stale_target`，不会修改文件或索引。

## 记忆生命周期

当前 v0.7.0 支持的真实生命周期：

```text
ChatGPT ZIP / manual import
→ raw 原始证据归档
→ 可读文本生成 text sidecar
→ index 写入 SQLite FTS
→ 人工或自动流程调用 apply 生成 candidate
→ review
→ accept / reject
→ active / archived / conflict / historical
```

raw 原始证据不会被 `index`、`db-rebuild`、`accept` 或 `reject` 删除。当前没有 recent purge 命令、删除宽限期命令或自动 recent 生命周期管理。

## Project 状态

```bash
python3 src/memory.py project-status \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --project pdc \
  --json
```

## Context Pack

```bash
python3 src/memory.py context \
  "本次任务查询" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --project pdc \
  --workspace personal \
  --format json \
  --max-chars 16000
```

```bash
python3 src/memory.py context \
  "本次任务查询" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --format markdown \
  --output context-pack.md
```

生成前会检查 SQLite 索引是否过期；过期时拒绝生成，提示先运行 `index`。

## Semantic / Hybrid 能力边界

已实现：

- lexical FTS5 检索
- 检索评测框架
- `--mode lexical|semantic|hybrid` 接口
- semantic/hybrid 后端不可用时的显式 lexical 回退

未实现：

- embedding 模型加载
- 文本向量生成
- chunk / embedding 表
- 向量持久化
- cosine similarity
- semantic 排序
- lexical + semantic 混合 RRF

## 保密规则

- `public` 和 `personal` 默认可导出
- `internal` 默认不导出，可用 `--include-internal` 显式包含
- `restricted` 永远不导出
- `internal` 和 `restricted` 必须属于 `work` workspace
- 搜索和 Context Pack 默认不返回 restricted 文档
- 密码、API key、SSH 私钥和其他凭证不得写入记忆库

## 备份与恢复

需要备份：

- Markdown 记忆
- raw 原始证据
- text 稳定文本副本
- `imports/document_metadata.json`
- `imports/chatgpt/import_manifest.json`
- 必要的项目资料

可以重建：

- `memory.sqlite`
- FTS 表
- 缓存
- 临时任务目录

恢复后执行：

```bash
python3 src/memory.py db-rebuild \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

SQLite 不是唯一备份对象，也不是权威数据源。

## 自动测试与 CI

本地发布门禁：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src
git diff --check
```

GitHub Actions 已配置在 push 和 pull request 时运行：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src
git diff --check
```

尚未推送分支或运行远程 workflow 时，不应声称远程 CI 已通过。

## 建议版本路线

```text
v0.8：LAOS 文档与边界修正，去文献系统主线残留
v0.9：Agent Registry + Orchestrator 最小骨架
v1.0：把现有能力包装为 Import / Memory / Review / Search / Context Agent
v1.1：Auto Update Loop 与低风险自动候选生成
v1.2：Link Understanding Agent 与 Hermes provider
v1.3：Rule / Reflection Agent
v1.4：Evolution Agent
v1.5：Controlled Agent Access / MCP / Codex Bridge
```

## 不属于 v0.7.0

iCloud 多端同步、设备注册、设备状态、移动端写入、自动索引刷新、实时同步、自动 ZIP 下载、ChatGPT 自动登录、Web 服务、GUI、桌面 App、云数据库、独立向量数据库和常驻后台监听服务不属于 v0.7.0，本次未实现。

## 许可证

代码使用 MIT License。
