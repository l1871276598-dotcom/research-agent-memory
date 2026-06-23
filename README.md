# Research Agent Memory

本项目是本地优先的个人科研 Agent 记忆库。代码仓库只保存代码、Schema、模板、测试和文档；真实记忆、未发表资料、PDF、原始导出 ZIP、SQLite 数据库、缓存和日志不得提交到 GitHub。

当前实现支持文件型 Markdown 记忆、工作区与保密级别隔离、情景迁移、SQLite FTS5 增量索引、关系链接、召回、ChatGPT 官方 ZIP 导入、手动投递箱导入，以及 recent -> 长期记忆 -> 安全删除的本地生命周期闭环。SQLite 是可删除、可重建的派生索引；raw 原始证据永久保留。

不包含 GUI、Obsidian 插件、TypeScript、Node.js、Chroma、图数据库、云数据库、实时常驻文件监听服务或 ChatGPT 账号自动登录下载。

## 目录布局

```text
GitHub 代码仓库
└── research-agent-memory/
    ├── src/
    ├── tests/
    ├── schemas/
    ├── templates/
    └── docs/

iCloud 数据目录
└── ResearchAgent/
    ├── memory/
    ├── imports/
    │   ├── chatgpt/conversations/
    │   └── manual/
    │       ├── inbox/
    │       ├── raw/
    │       └── quarantine/
    ├── literature/
    ├── manuscripts/
    ├── exports/
    └── backups/

Mac 本地运行目录
└── ~/Library/Application Support/ResearchAgent/
    ├── memory.sqlite
    ├── memory.lock
    ├── distillation/
    └── *.log
```

活动 SQLite、缓存和日志必须放在 Mac 本地运行目录，不应放在 iCloud 数据目录中。

## 核心命令

```bash
python3 src/memory.py init --root PATH
python3 src/memory.py add ...
python3 src/memory.py validate --root PATH
python3 src/memory.py export --root PATH
python3 src/memory.py context-transition ...
python3 src/memory.py db-init --root PATH --state-dir STATE
python3 src/memory.py index --root PATH --state-dir STATE
python3 src/memory.py search QUERY --root PATH --state-dir STATE
```

已实现的核心能力包括：

- 标准数据目录初始化
- `profile`、`context`、`principle`、`project`、`decision`、`procedure`、`session` 等结构化记忆
- `memory/v1` 与 `memory/v2` 文档验证
- workspace 与保密级别隔离
- 确定性 JSONL 导出和 SHA-256 清单
- 事务式情景迁移
- SQLite schema v2、FTS5、中文二元词检索和增量索引
- 双链解析、未解析链接检查和召回

## 扩展命令

`memory_tools.py` 提供兼容入口和轻量工具：

```bash
python3 src/memory_tools.py search QUERY --root PATH --state-dir STATE
python3 src/memory_tools.py import-chatgpt --zip chatgpt-export.zip --root PATH --state-dir STATE
python3 src/memory_tools.py import-manual --root PATH --state-dir STATE --scan-inbox
python3 src/memory_tools.py recall QUERY --root PATH --state-dir STATE --json
python3 src/memory_tools.py backlinks ID --root PATH --state-dir STATE --json
python3 src/memory_tools.py outgoing ID --root PATH --state-dir STATE --json
python3 src/memory_tools.py related ID --root PATH --state-dir STATE --json
python3 src/memory_tools.py unresolved --root PATH --state-dir STATE --json
python3 src/memory_tools.py check-links --root PATH --state-dir STATE --json
python3 src/memory_tools.py launchd-plist --root PATH --state-dir STATE
```

`memory_tools.py search` 保留旧命令名称，但内部调用当前 `memory.py` 的 SQLite v2 搜索实现；`import-chatgpt` 保留旧入口，但内部调用 `chatgpt_export_sync.py import-zip`。

## 记忆生命周期

权威事实来源是 Markdown 记忆和 raw 原始证据。统一流程如下：

```text
ChatGPT 官方 ZIP / imports/manual/inbox/
-> imports/chatgpt/conversations 或 imports/manual/raw 永久归档
-> memory/recent 生成最近正文
-> SQLite FTS5 + links 增量索引
-> memory_distill.py prepare/run/apply
-> memory/<type> 长期 memory/v2
-> 蒸馏成功后保留 7 天宽限期
-> purge 在全部门禁通过后只删除对应 memory/recent/*.md
```

`memory/recent/` 保留最近 30 天正文。Codex 只在隔离任务目录中执行语义蒸馏并返回标准 JSON；Python 负责 JSON 校验、长期记忆合并、冲突检测、双链、SQLite 更新和删除门禁。raw 原始证据不会被蒸馏或清理流程删除。

删除 recent 前必须通过这些门禁：

- recent 已到 30 天保留边界
- 蒸馏审计成功且 7 天宽限期已过
- raw 原始证据存在且 SHA-256 匹配
- 长期记忆合并结果通过 schema 验证
- SQLite 索引可重建且一致
- 失败、冲突、未完成或受保护记录一律不删除

## 快速开始

```bash
DATA_ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent"
STATE_DIR="$HOME/Library/Application Support/ResearchAgent"
```

初始化数据目录和数据库：

```bash
python3 src/memory.py init --root "$DATA_ROOT"
python3 src/memory.py db-init --root "$DATA_ROOT" --state-dir "$STATE_DIR"
```

添加一条长期记忆：

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

验证并重建索引：

```bash
python3 src/memory.py validate --root "$DATA_ROOT"
python3 src/memory.py index --root "$DATA_ROOT" --state-dir "$STATE_DIR"
```

搜索：

```bash
python3 src/memory.py search "代码最少" --root "$DATA_ROOT" --state-dir "$STATE_DIR"
python3 src/memory_tools.py search "代码最少" --root "$DATA_ROOT" --state-dir "$STATE_DIR" --json
```

## ChatGPT ZIP 导入

当前实现是官方 ZIP 导入，不是自动登录下载，也不会访问 ChatGPT 账号或实时同步。

先预演：

```bash
python3 src/memory_tools.py import-chatgpt \
  --zip "$HOME/Downloads/chatgpt-export.zip" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --dry-run
```

正式导入：

```bash
python3 src/memory_tools.py import-chatgpt \
  --zip "$HOME/Downloads/chatgpt-export.zip" \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

导入会读取 ZIP 中的 `conversations.json`，将对话 Markdown 永久归档到：

```text
imports/chatgpt/conversations/YYYY/MM/*.md
```

同时生成 `memory/recent/recent-chatgpt-*.md` 并更新 SQLite 索引。重复导入相同内容不会重复创建；对话内容变化时会更新对应 raw 和 recent 文件。当前只处理文本对话，不导入图片、附件和语音文件。

也可以直接调用底层入口：

```bash
python3 src/chatgpt_export_sync.py import-zip \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --zip "$HOME/Downloads/chatgpt-export.zip" \
  --json
```

## 手动导入

把文档放入投递箱：

```bash
cp note.txt "$DATA_ROOT/imports/manual/inbox/"
python3 src/memory_tools.py import-manual \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --scan-inbox \
  --json
```

导入流程会等待文件稳定，计算 SHA-256 去重，永久归档到 `imports/manual/raw/`，提取文本生成 `memory/recent/`，失败文件进入 `imports/manual/quarantine/`，成功后增量更新 SQLite。

可用 `launchd-plist` 生成 macOS launchd 配置；它只触发批量导入命令，不是常驻实时文件监听服务。

## 蒸馏与清理

```bash
python3 src/memory_distill.py prepare --root "$DATA_ROOT" --state-dir "$STATE_DIR" --json
python3 src/memory_distill.py run --task-dir "$STATE_DIR/distillation/tasks/TASK" --codex-bin codex --json
python3 src/memory_distill.py apply --root "$DATA_ROOT" --state-dir "$STATE_DIR" --task-dir "$STATE_DIR/distillation/tasks/TASK" --json
python3 src/memory_distill.py purge --root "$DATA_ROOT" --state-dir "$STATE_DIR" --json
```

`prepare` 创建隔离任务目录；`run` 调用 Codex 并要求标准 JSON；`apply` 校验 JSON、合并长期记忆、记录冲突和审计状态；`purge` 只在门禁全部通过后删除对应 recent 文件。raw 归档不属于 purge 删除范围。

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
- 真实记忆、PDF、原始 ChatGPT ZIP、SQLite、缓存、日志和内部资料不得提交到 GitHub。

## 自动测试

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall src
git diff --check
```

当前测试覆盖初始化、v1/v2 schema、SQLite v2、FTS5、ChatGPT ZIP 幂等、手动导入、双链、recall、Codex 蒸馏模拟、30+7 删除门禁、raw 保留和失败安全。

## 尚未实现

- ChatGPT 账号实时自动同步或自动登录下载
- ChatGPT 附件导入
- PDF 自动解析和文献矩阵自动填充
- Zotero 或 EndNote 同步
- Chroma、向量嵌入和语义检索
- MCP 接口
- 总控 Agent

## 许可证

代码使用 MIT License。
