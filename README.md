# Research Agent Memory

## 项目简介

这是一个本地优先的个人科研 Agent 记忆库。

- GitHub 保存代码、Schema、模板、测试和文档。
- iCloud 数据目录保存 Markdown 记忆、JSONL、文献笔记、文献矩阵和允许同步的归档文件。
- Mac 本地运行目录保存 SQLite、缓存和日志。

真实记忆、未发表资料、PDF 和运行数据库不得进入 GitHub。活动 SQLite 数据库不得直接放入 iCloud。

## 当前能力

### 核心记忆命令

```bash
python3 src/memory.py init --root PATH
python3 src/memory.py add ...
python3 src/memory.py validate --root PATH
python3 src/memory.py export --root PATH
python3 src/memory.py context-transition ...
python3 src/memory.py db-init --root PATH
python3 src/memory.py index --root PATH
python3 src/memory.py db-rebuild --root PATH
python3 src/memory.py search "QUERY" --root PATH
python3 src/memory.py document-meta set ...
python3 src/memory.py project-status --project PROJECT --root PATH
python3 src/memory_distill.py review --root PATH
```

已实现：

- 标准数据目录初始化
- `profile`、`context`、`principle`、`project`、`decision`、`procedure`、`session` 等结构化记忆
- 单文件与跨文件验证
- workspace 与保密级别隔离
- 确定性 JSONL 导出和 SHA-256 清单
- 事务式情景迁移
- 本地 SQLite FTS5 初始化
- Markdown 记忆、ChatGPT 原始归档、手工原文、文献笔记和稿件文件增量索引
- 统一全文搜索，可按记忆/文档、来源、项目和 workspace 过滤
- 项目注册校验、文档元数据覆盖、时间有效性过滤和项目状态汇总
- 中文二元词索引预处理
- 候选记忆审核生命周期：candidate → accept/reject → active/accepted/rejected/conflict

### 轻量扩展命令

ChatGPT ZIP 和手工文件导入暂放在一个轻量入口中：

```bash
python3 src/memory_tools.py import-chatgpt ...
python3 src/memory_tools.py import-manual ...
```

已实现：

- 从 ChatGPT 官方导出 ZIP 中识别 `conversations.json`
- ZIP 预检、CRC 检查、重复 `conversations.json` 检查和导入报告
- 将本地可读文件保存为 raw 证据和稳定 Markdown 文本副本
- 使用对话 ID 和内容哈希进行幂等更新
- `--dry-run` 预演
- 导入清单生成

导入只负责原始证据档案，不会自动把每段对话或文件晋升为长期记忆。

## 推荐目录

```text
GitHub 代码仓库
└── research-agent-memory/
    ├── src/
    ├── tests/
    ├── schemas/
    ├── templates/
    └── .github/workflows/

iCloud 数据目录
└── ResearchAgent/
    ├── memory/
    ├── literature/
    ├── manuscripts/
    ├── imports/chatgpt/
    ├── exports/
    └── backups/

Mac 本地运行目录
└── ~/Library/Application Support/ResearchAgent/
    └── memory.sqlite
```

## 快速开始

以下示例使用建议路径：

```bash
DATA_ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent"
STATE_DIR="$HOME/Library/Application Support/ResearchAgent"
```

### 1. 初始化数据目录

```bash
python3 src/memory.py init --root "$DATA_ROOT"
```

### 2. 添加一条记忆

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

### 3. 验证记忆库

```bash
python3 src/memory.py validate --root "$DATA_ROOT"
```

### 4. 初始化并更新本地索引

```bash
python3 src/memory.py db-init \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"

python3 src/memory.py index \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

可先预演索引变化：

```bash
python3 src/memory.py index \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --dry-run
```

如需从 Markdown 和原始文本证据重建 SQLite 派生索引：

```bash
python3 src/memory.py db-rebuild \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

## 搜索记忆

基础搜索：

```bash
python3 src/memory.py search "代码最少" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

按项目和来源过滤：

```bash
python3 src/memory.py search "论文证据链" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --project pdc-rock-manuscript \
  --kind document \
  --source-kind manuscript
```

输出 JSON：

```bash
python3 src/memory.py search "RMRE" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --json
```

搜索前必须先完成 `db-init` 和 `index`。索引过期时搜索会拒绝返回结果，并提示先重新运行 `index`。

默认搜索会合并结构化记忆和已索引文档。文档来源包括：

- `imports/chatgpt/conversations/`
- `imports/manual/raw/`
- `literature/notes/`
- `literature/journals/`
- `manuscripts/current/`
- `manuscripts/evidence/`
- `manuscripts/archive/`

## 导入 ChatGPT 官方导出

手动流程：

```text
用户在 ChatGPT 中申请数据导出
→ 用户自行从邮件下载 ZIP
→ 手动运行 import-chatgpt
→ 归档为 Markdown
```

先预演：

```bash
python3 src/memory_tools.py import-chatgpt \
  --zip "$HOME/Downloads/chatgpt-export.zip" \
  --root "$DATA_ROOT" \
  --dry-run
```

正式导入：

```bash
python3 src/memory_tools.py import-chatgpt \
  --zip "$HOME/Downloads/chatgpt-export.zip" \
  --root "$DATA_ROOT"
```

输出位置：

```text
imports/chatgpt/conversations/YYYY/MM/*.md
imports/chatgpt/import_manifest.json
```

重复导入相同内容不会重复创建对话文件；对话内容发生变化时会更新原文件。

该仓库只支持用户自行下载官方导出 ZIP 后再运行 `import-chatgpt`；重复导入保持幂等。

## 导入手工文件

手工文件导入会保存一份 raw 原文件，并为可直接提取文本的文件生成稳定 Markdown 文本副本：

```bash
python3 src/memory_tools.py import-manual \
  --path "$HOME/Downloads/note.txt" \
  --root "$DATA_ROOT"
```

输出位置：

```text
imports/manual/raw/YYYY/MM/*
imports/manual/text/YYYY/MM/*.md
exports/import_reports/*.json
```

## 诊断

```bash
python3 src/memory.py doctor \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

`doctor` 只读检查数据根、SQLite schema、WAL、memory/document 索引新鲜度、哈希不一致和手工 raw/text 孤立文件。

## 候选记忆审核

Codex 或其他自动蒸馏流程应先生成候选，不直接写入正式 active 记忆：

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

python3 src/memory_distill.py review --root "$DATA_ROOT"
python3 src/memory_distill.py accept --root "$DATA_ROOT" --id CANDIDATE_ID
python3 src/memory_distill.py reject --root "$DATA_ROOT" --id CANDIDATE_ID --reason "证据不足"
```

`merge` 和 `support` 只合并 `source_refs`、`tags`、`relations` 等安全列表，不静默覆盖目标记忆的核心 `content`。

## 项目治理

项目作用域记忆必须引用已有 active `type: project` 记录。同一 project 只能有一个 active project 注册记录。

文档项目元数据保存在权威文件 `imports/document_metadata.json`：

```bash
python3 src/memory.py document-meta set \
  --root "$DATA_ROOT" \
  --path imports/manual/raw/note.txt \
  --project pdc-rock-manuscript \
  --workspace personal \
  --confidentiality personal

python3 src/memory.py document-meta unset \
  --root "$DATA_ROOT" \
  --path imports/manual/raw/note.txt
```

搜索支持时间点过滤：

```bash
python3 src/memory.py search "论文证据链" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --project pdc-rock-manuscript \
  --as-of 2026-06-24
```

查看项目状态：

```bash
python3 src/memory.py project-status \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --project pdc-rock-manuscript
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

旧 Context 不会被删除，而是转为 `historical`，并通过 `supersedes` 和 `superseded_by` 建立关系。

## 保密规则

- `public` 和 `personal` 默认可导出。
- `internal` 默认不导出，可用 `--include-internal` 显式包含。
- `restricted` 永远不导出。
- `internal` 和 `restricted` 必须属于 `work` workspace。
- 密码、API key、SSH 私钥和其他凭证不得写入记忆库。
- 原始 ChatGPT 导出 ZIP、真实记忆、PDF、SQLite 和 token 不得提交到 GitHub。

## 自动测试

GitHub Actions 在 push 和 pull request 时自动执行：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall src
```

本地也可以运行相同命令。

## 尚未实现

- 对话自动提取并审核候选长期记忆
- ChatGPT 附件导入
- 官方 ZIP 与实时快照自动对账
- Mac 离线时的加密云队列
- PDF 自动解析和文献矩阵自动填充
- Zotero 或 EndNote 同步
- Chroma、向量嵌入和语义检索
- MCP 接口
- 总控 Agent

## 许可证

代码使用 MIT License。
