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
- 中文二元词索引预处理

### 轻量扩展命令

ChatGPT 导入和实时归档暂放在一个轻量入口中：

```bash
python3 src/memory_tools.py import-chatgpt ...
python3 src/memory_tools.py serve-chatgpt ...
```

已实现：

- 从 ChatGPT 官方导出 ZIP 中识别 `conversations.json`
- 将当前活动分支的文本消息归档为 Markdown
- 使用对话 ID 和内容哈希进行幂等更新
- `--dry-run` 预演
- 导入清单生成
- 通过私有 GPT Action 接收当前对话上下文快照
- Bearer token 鉴权、请求大小限制和仅 loopback 监听
- 实时归档的幂等创建与更新

ChatGPT 官方导入和实时归档都只负责原始对话档案，不会自动把每段对话晋升为长期记忆，也不会导入附件或所有分支版本。

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
    ├── memory.sqlite
    └── chatgpt_archive.token
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

该仓库不接入 Gmail，不需要 Google OAuth，不自动下载 ZIP，也不安装同步 LaunchAgent。用户自行下载官方导出 ZIP 后再运行 `import-chatgpt`；重复导入保持幂等。

## 实时归档当前 ChatGPT 对话

实时归档不依赖官方 ZIP。它使用私有 Custom GPT Action 将当前模型上下文中的可见 `user`/`assistant` 消息发送到 Mac。

生成本地 token：

```bash
mkdir -p "$STATE_DIR"
umask 077
python3.13 -c 'import secrets; print(secrets.token_urlsafe(48))' \
  > "$STATE_DIR/chatgpt_archive.token"
```

启动本地接收服务：

```bash
python3.13 src/memory_tools.py serve-chatgpt \
  --root "$DATA_ROOT" \
  --token-file "$STATE_DIR/chatgpt_archive.token"
```

服务只监听 `127.0.0.1`。通过 Tailscale Funnel 暴露 HTTPS 入口：

```bash
tailscale funnel --bg 8765
tailscale funnel status
```

归档输出：

```text
imports/chatgpt/live/YYYY/MM/*.md
```

完整的 Custom GPT 指令、OpenAPI schema、安全要求和限制见：

```text
docs/chatgpt_live_archive.md
```

在普通 ChatGPT 对话中可通过 `@你的归档GPT 归档本次` 调用。写操作可能需要在 ChatGPT 中确认。

实时归档的限制：

- 仅保存模型当前可见上下文，不等同于账户级原始导出
- 只接受可见 `user` 和 `assistant` 消息
- 不接受 system、developer、tool 或隐藏消息
- 默认不保证长对话的完整历史
- Mac 必须在线、本地接收服务与 Funnel 必须运行
- 官方 ZIP 仍是完整历史的权威归档来源
- `imports/chatgpt/` 尚未纳入正式 SQLite 记忆索引

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
- 原始 ChatGPT 导出 ZIP、真实记忆、PDF、SQLite 和归档 token 不得提交到 GitHub。
- 实时接收服务必须只监听 loopback，并通过带 Bearer token 的 HTTPS 隧道访问。

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
- 将原始对话纳入统一搜索
- 官方 ZIP 与实时快照自动对账
- Mac 离线时的加密云队列
- PDF 自动解析和文献矩阵自动填充
- Zotero 或 EndNote 同步
- Chroma、向量嵌入和语义检索
- MCP 接口
- 总控 Agent

## 许可证

代码使用 MIT License。
