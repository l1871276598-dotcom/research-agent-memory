# Phase 1 数据级验收

## 执行日期

2026-06-22

## 代码提交

- 当前提交 SHA：`98378f4`
- 提交信息：`docs: add phase 1 usage and code acceptance`

## 数据根目录

```text
/Users/user/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent
```

## 根目录与代码仓库分离检查

代码仓库：

```text
/Users/user/Library/Mobile Documents/com~apple~CloudDocs/codex/记忆库
```

数据根目录：

```text
/Users/user/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent
```

检查结果：

```text
Path separation: OK
```

数据根目录不等于代码仓库目录，也不位于代码仓库内部。

## init 首次结果

执行：

```bash
python3 src/memory.py init --root "$RA_DATA"
```

结果：

```text
数据根目录: /Users/user/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent
新建目录数量: 18
新建文件数量: 3
已存在项目数量: 0
```

## init 第二次幂等结果

再次执行：

```bash
python3 src/memory.py init --root "$RA_DATA"
```

结果：

```text
数据根目录: /Users/user/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent
新建目录数量: 0
新建文件数量: 0
已存在项目数量: 21
```

`.research-agent-root` 和 `exports/index_manifest.json` 均可被 `python3 -m json.tool` 正常解析。

## 实际创建的基础记忆列表

本次防重复检查未发现已有目标记录，因此创建以下 8 条基础记忆：

| 类型 | 标题或项目 | 路径 |
| --- | --- | --- |
| context | 个人科研与项目开发阶段 | `memory/contexts/context-20260622-a82ececf-个人科研与项目开发阶段.md` |
| profile | 个人科研与开发偏好 | `memory/profile/profile-20260622-4c50bfaa-个人科研与开发偏好.md` |
| principle | 代码最少原则 | `memory/principles/principle-20260622-806b654b-代码最少原则.md` |
| principle | 事实、来源与确认状态原则 | `memory/principles/principle-20260622-d8c50e20-事实-来源与确认状态原则.md` |
| project | research-agent-memory | `memory/projects/project-20260622-33dc248f-Research-Agent-Memory.md` |
| project | journal-manuscript-agent | `memory/projects/project-20260622-c78d5652-Journal-Manuscript-Agent.md` |
| project | pdc-rock-paper | `memory/projects/project-20260622-ac029c6e-PDC-rock-Drilling-Fluid-Immersion-Manuscript.md` |
| decision | 记忆库三层存储架构 | `memory/decisions/decision-20260622-1dd506d8-记忆库三层存储架构.md` |

## 已存在而跳过的记忆列表

无。初始化前 `memory/` 下没有 Markdown 记忆文件，front matter 检查结果为空：

```text
context_id: []
project: []
title: []
```

## validate 文件数量与错误数量

执行：

```bash
python3 src/memory.py validate --root "$RA_DATA"
```

结果：

```text
Validated: 8 files
Errors: 0
Warnings: 0
```

## export 记录数量

执行：

```bash
python3 src/memory.py export --root "$RA_DATA"
```

结果：

```text
Exported: 8 records
Skipped internal: 0
Skipped restricted: 0
JSONL records: 8
```

## export 文件 SHA-256

说明：本机 `shasum` 在继承 `C.UTF-8` locale 时会失败，因此本次使用 `LC_ALL=C LANG=C shasum -a 256` 计算哈希。

```text
9e9af8e082a8fdd94dfd57b8b2d1d7d7e1842ad6a888660b308746b4299260a8  /Users/user/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent/exports/memory.jsonl
92a522cef9e63c71d427e694b0b08e32e3945b2b7fe1e46093b219fb9f9d3b0a  /Users/user/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent/exports/index_manifest.json
```

## 数据目录边界检查结果

数据目录文件清单包含：

- `.research-agent-root`
- `exports/index_manifest.json`
- `exports/memory.jsonl`
- `literature/literature_matrix.csv`
- 8 个 Markdown 记忆文件

执行以下禁用项扫描：

```bash
find "$RA_DATA" \
  \( -name ".git" \
  -o -name "*.sqlite" \
  -o -name "*.sqlite3" \
  -o -name "*.sqlite-wal" \
  -o -name "*.sqlite-shm" \
  -o -name "id_ed25519*" \
  -o -name ".env" \) \
  -print
```

结果：无输出。数据目录内未发现 Git 仓库、SQLite、Chroma、私钥或 `.env`。

## GitHub 仓库数据边界检查结果

执行：

```bash
git ls-files | grep -E '(^|/)(memory\.jsonl|.*\.sqlite|.*\.sqlite3|.*\.pdf)$' \
  && echo "ERROR: unexpected tracked data" \
  || echo "Repository data boundary: OK"
```

结果：

```text
Repository data boundary: OK
```

代码仓库没有跟踪真实记忆导出、PDF 或数据库文件。

## 尚未完成的 Phase 2 功能

- SQLite FTS5 精确检索尚未实现。
- SQLite 索引构建命令尚未实现。
- SQLite 本地运行目录策略尚未落地。
- Phase 3 的 Chroma/Ollama、Phase 4 的 MCP、Phase 5 的文献自动化和 Phase 6 的总控 Agent 尚未开始实施。

## 最终结论

Phase 1 文件型记忆库的数据级验收通过。真实 iCloud 数据目录已初始化，基础记忆已录入并成功验证与导出；Phase 1 可以标记为 completed，Phase 2 可以解锁，但尚未开始实施。
