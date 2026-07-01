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

- 当前软件版本：v0.8.0
- SQLite schema：v3
- 默认检索模式：lexical
- 主要支持平台：macOS
- 支持的 Python：3.11 或更高版本
- 默认不需要 Codex CLI、语义模型、云 API 或付费 API

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
- 可插拔 ModelBackend、Codex 与 OpenAI-compatible 后端
- Agent Runtime、Tool Registry、SessionStore 和 Procedure 生命周期
- Bridge Event Inbox、Session Projector、自动候选审查和崩溃恢复
- MCP stdio 与回环 Streamable HTTP 服务
- 可选显式 `laos_capture_checkpoint` 工具和实机验证流程
- `doctor` 健康检查
- GitHub Actions 在 push / pull_request 运行测试、compileall 和 whitespace 检查

## 尚未完成或尚未验证的能力

- 真实 ChatGPT MCP checkpoint 五轮实测与正式定级
- 浏览器侧无损对话事件采集源
- 完整统一 Runtime 与默认 Combined Context
- 长会话 Context Compactor 默认接线
- 轻量 Loop Engineering 的任务结果闭环
- ChatGPT 附件导入
- PDF/DOCX 深度结构化解析
- Link Understanding Agent / provider
- owner/agent 身份认证与多 Agent 授权
- 内置文献管理、文献矩阵自动填充和 Zotero / EndNote 同步已移出路线；后续如有需要，仅通过外部接口或 Adapter 集成
- 真实本地向量 embedding
- 真实语义相似度检索
- 真实 lexical + semantic 混合排序

MCP checkpoint 已具备代码和验证框架，但其可用性受 ChatGPT 计划、远程连接和写操作权限限制，不能描述为被动或无损对话采集。

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

`literature/` 是旧版本兼容和历史残留目录，不再作为内置文献系统主线。未来应迁移为 External Adapter 输出目录，并可在后续版本中进一步移除或降级。

本地 state 目录至少包含：

```text
~/Library/Application Support/ResearchAgent/
├── memory.sqlite
├── sessions.sqlite
├── bridge_events.sqlite
└── review_state.sqlite
```

SQLite、WAL、SHM、锁、缓存和日志都属于可重建的本地派生状态，不应放在 iCloud 数据目录。

## 快速开始

```bash
REPO_ROOT="/path/to/laos-v0.8"
DATA_ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent"
STATE_DIR="$HOME/Library/Application Support/ResearchAgent"
cd "$REPO_ROOT"
```

```bash
python3 tools/setup_local.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --profile personal \
  --workspace personal
```

运行完整测试：

```bash
python3 -m unittest discover -s tests -v
```

MCP checkpoint 验证：

```bash
python3 -m pip install -r requirements-mcp.txt
python3 tools/mcp_checkpoint_trial.py prepare \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --workspace personal
python3 tools/mcp_checkpoint_trial.py serve --state-dir "$STATE_DIR"
```

详细流程见 `docs/mcp_checkpoint_validation.md`。
