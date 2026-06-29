# Research Agent Memory

本仓库是本地优先的个人科研 Agent 记忆库。GitHub 只保存代码、Schema、模板、测试和文档；Markdown 记忆、raw 原始证据和稳定文本副本保存在用户选择的数据目录；活动 SQLite、WAL/SHM、缓存和日志必须保存在本地 state 目录。

真实记忆、真实 ChatGPT ZIP、真实聊天记录、未发表资料、PDF、SQLite、日志、缓存、API key、密码、SSH 私钥和 token 不得提交到 GitHub。

## 当前版本状态

- 当前软件版本：v0.7.0
- SQLite schema：v3
- 默认检索模式：lexical
- 主要支持平台：macOS
- 支持的 Python：3.11 或更高版本

## 环境要求

- Python 3.11+
- 标准库 `sqlite3` 必须启用 FTS5
- macOS 是主要使用平台；CI 使用 Ubuntu + Python 3.11
- 当前代码不调用 `pdftotext` 或 `textutil`
- 当前代码不需要 Codex CLI、语义模型、云 API 或付费 API

没有文本提取器时，`import-manual` 对 PDF/DOCX 只归档 raw 原始文件，报告 `archived_without_text: 1`，不会生成 text sidecar，也不会进入全文索引。要索引 PDF/DOCX 内容，需要先用外部工具生成 Markdown/TXT，再导入该文本文件。

## 当前能力

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

- 新导入对话自动批量生成候选记忆
- ChatGPT 附件导入
- PDF/DOCX 深度结构化解析
- 文献矩阵自动填充
- Zotero / EndNote 同步
- 真实本地向量 embedding
- 真实语义相似度检索
- 真实 lexical + semantic 混合排序
- MCP 接口
- owner/agent 身份认证与多 Agent 授权
- 自动语义冲突合并
- 向量数据库、GUI 和大型 coordinator / 总控 Agent

候选审核生命周期已经实现；自动候选生成调度尚未实现。

## 目录布局

`init` 会创建以下数据目录和文件：

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
```

```bash
python3 src/memory.py index \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

```bash
python3 src/memory.py search "代码最少" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

```bash
python3 src/memory.py doctor \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
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

JSON 输出形如：

```json
{
  "requested_mode": "lexical",
  "effective_mode": "lexical",
  "warnings": [],
  "results": []
}
```

当前索引源：

- `imports/chatgpt/conversations/`
- `imports/manual/text/`
- `imports/manual/raw/`，仅当没有对应 text sidecar 且 raw 是 UTF-8 可读文本
- `literature/notes/`
- `literature/journals/`
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

## Doctor

```bash
python3 src/memory.py doctor \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --json
```

`doctor` 检查数据根、SQLite schema、WAL、memory/document 索引新鲜度、哈希不一致、manual raw/text 孤立文件和旧网络残留标记。

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

成功输出是单个 JSON 对象，包含 `operation: "prepare"`、`context`、`context_chars`、`max_chars`、真实召回来源 `sources` 和 `warnings`。`context_chars` 始终等于 Python `len(context)`，且不超过 `max_chars`。

```bash
python3 src/memory_agent.py finalize \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task "整理 PDC 项目的证据链" \
  --result-file completed-result.txt
```

短结果可改用 `--result "..."`；`--result` 与 `--result-file` 互斥。成功输出包含 `operation: "finalize"`、真实 `candidate_count`、真实候选 `artifacts`、`review_required: true`、`applied: false` 和 `warnings`。第一版只进入现有候选审核队列，候选必须人工审核，绝不自动应用到权威记忆。

预期错误同样返回单个 JSON 对象，包含稳定的 `error.code` 和安全的 `error.message`，并以非零状态退出。Windows、macOS 和 Linux 使用相同命令；省略 `--state-dir` 时继续使用 `platform_paths.py` 的平台本地状态目录，SQLite 不写入同步数据目录。

## 候选审核

候选审核只有一套当前实现的流程：

```text
apply
→ review
→ accept / reject
```

当前 `memory_distill.py` 不提供 `prepare`、`run`、`purge` 或 `status` 子命令。

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
python3 src/memory_distill.py review \
  --root "$DATA_ROOT" \
  --json
```

```bash
python3 src/memory_distill.py accept \
  --root "$DATA_ROOT" \
  --id CANDIDATE_ID
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

## 项目治理

```bash
python3 src/memory.py document-meta set \
  --root "$DATA_ROOT" \
  --path imports/manual/text/2026/06/note.md \
  --project pdc \
  --workspace personal \
  --confidentiality personal
```

```bash
python3 src/memory.py document-meta unset \
  --root "$DATA_ROOT" \
  --path imports/manual/text/2026/06/note.md
```

`document-meta` 使用相对 `DATA_ROOT` 的 `--path`。路径必须属于真实索引源，不能逃逸到 data root 外部。

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

## 检索评测

评测模板在 `templates/retrieval_eval.json`。真实评测数据应放在用户数据目录，不提交仓库。

```bash
python3 src/memory.py evaluate-search \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --cases "$DATA_ROOT/evaluation/retrieval_cases.json" \
  --mode lexical \
  --json
```

输出包含 `requested_mode`、`effective_mode`、`warnings`、Top-1/Top-5、泄漏率和延迟统计。

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

示例：

```bash
python3 src/memory.py search "PDC" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --mode hybrid
```

文本输出会显示：

```text
Requested mode: hybrid
Effective mode: lexical
Warning: hybrid search unavailable; falling back to lexical
```

## 情景迁移

```bash
python3 src/memory.py context-transition \
  --root "$DATA_ROOT" \
  --from-context university-student \
  --to-context industry-engineer \
  --to-title "企业研发阶段" \
  --workspace work \
  --confidentiality internal \
  --effective-date 2027-07-01 \
  --reason "从学校科研阶段进入企业研发阶段" \
  --dry-run
```

`context-transition` 只创建一条 transition candidate，不直接修改旧 context 或创建 active 新 context。accept 重新验证旧记录状态和 hash 后，在同一文件事务中将旧记录转为 `deprecated`、创建 active 新记录、归档 accepted transition，并维护 `supersedes` / `superseded_by`；随后才 reindex 和 verify。

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

## 不属于 v0.7.0

iCloud 多端同步、设备注册、设备状态、移动端写入、自动索引刷新、实时同步、自动 ZIP 下载、ChatGPT 自动登录、Web 服务、GUI、桌面 App、云数据库、独立向量数据库和常驻后台监听服务不属于 v0.7.0，本次未实现。

## 许可证

代码使用 MIT License。
