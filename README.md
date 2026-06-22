# Research Agent Memory

## 项目简介

这是一个本地优先的个人科研 Agent 记忆库。GitHub 保存代码、规则、Schema、模板、测试和文档；iCloud 数据目录保存 Markdown、JSONL、文献笔记、文献矩阵和允许同步的文件；未来的 SQLite、Chroma、缓存和日志放在 Mac 本地运行目录。

当前项目支持文件型记忆、工作区与保密级别隔离、情景迁移，并可在后续阶段扩展到 SQLite FTS5、语义搜索和 MCP 接口。

## 当前版本能力

Phase 1 已实现以下命令：

```bash
python3 src/memory.py init --root PATH
python3 src/memory.py add ...
python3 src/memory.py validate --root PATH
python3 src/memory.py export --root PATH
python3 src/memory.py context-transition ...
```

当前未实现：

- SQLite FTS5
- Chroma
- Ollama
- MCP
- 文献自动抓取
- 总控 Agent 接入

## 存储职责边界

GitHub 保存：

- 代码
- Schema
- 模板
- 测试
- 文档
- 示例配置

iCloud 数据目录保存：

- Markdown 记忆
- JSONL
- 文献笔记
- 文献矩阵
- 允许同步的导出文件

Mac 本地运行目录保存未来运行时数据：

- SQLite
- Chroma
- 缓存
- 日志

活动数据库不得直接放入 iCloud。真实记忆、PDF、内部资料和数据库不得进入 GitHub。

## 快速开始

以下命令只使用临时目录，可以直接复制执行：

```bash
rm -rf /tmp/research-agent-demo

python3 src/memory.py init --root /tmp/research-agent-demo

python3 src/memory.py add \
  --root /tmp/research-agent-demo \
  --type principle \
  --title "代码最少原则" \
  --scope global \
  --workspace personal \
  --confidentiality personal \
  --source user \
  --confidence confirmed \
  --content "使用尽可能少的代码实现相同功能。" \
  --tags coding architecture

python3 src/memory.py add \
  --root /tmp/research-agent-demo \
  --type context \
  --title "研究生阶段" \
  --scope context \
  --workspace personal \
  --confidentiality personal \
  --source user \
  --confidence confirmed \
  --content "当前处于个人科研学习阶段。" \
  --context-id university-student \
  --valid-from 2023-09-01 \
  --tags education research

python3 src/memory.py validate --root /tmp/research-agent-demo
python3 src/memory.py export --root /tmp/research-agent-demo
```

## 情景变化示例

从 `university-student` 迁移到 `industry-engineer`：

```bash
python3 src/memory.py context-transition \
  --root /tmp/research-agent-demo \
  --from-context university-student \
  --to-context industry-engineer \
  --to-title "企业研发阶段" \
  --workspace work \
  --confidentiality internal \
  --effective-date 2027-07-01 \
  --reason "从学校科研阶段进入企业研发阶段" \
  --dry-run

python3 src/memory.py context-transition \
  --root /tmp/research-agent-demo \
  --from-context university-student \
  --to-context industry-engineer \
  --to-title "企业研发阶段" \
  --workspace work \
  --confidentiality internal \
  --effective-date 2027-07-01 \
  --reason "从学校科研阶段进入企业研发阶段"

python3 src/memory.py validate --root /tmp/research-agent-demo
```

旧 Context 不会被删除，而是转为 `historical`，并通过 `superseded_by` 与新 Context 建立关系。

## 保密规则

- `public` 和 `personal` 默认可导出。
- `internal` 默认不导出。
- `--include-internal` 可显式包含 `internal`。
- `restricted` 永远不导出。
- `internal` 和 `restricted` 必须属于 `work` workspace。

## 测试

当前验收使用：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall src
```

最近一次代码级验收结果：59 个测试全部通过，`compileall` 通过。

## 许可证

代码使用 MIT License。
