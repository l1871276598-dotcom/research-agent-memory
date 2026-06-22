# Phase 1 代码级验收

## 验收日期

2026-06-22

## 验收范围

本次验收覆盖 Phase 1 文件型基础记忆库的代码级能力：

- 数据根目录初始化
- Markdown 记忆创建
- 记忆结构和跨文件引用验证
- JSONL 和 manifest 导出
- internal/restricted 导出过滤
- 情景迁移 dry-run
- 正式情景迁移
- 事务写入和回滚测试

验收只使用临时目录 `/tmp/research-agent-phase1-acceptance`，未使用真实 iCloud 数据目录。

## 当前提交

- 当前提交 SHA：`9c029d6`
- 提交信息：`feat: add transactional context transitions`

## Python 版本

```text
Python 3.9.6
```

## 测试结果

执行命令：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall src
```

结果：

- 测试总数：59
- 测试结果：全部通过
- `compileall`：通过

## init 幂等结果

执行两次初始化：

```bash
python3 src/memory.py init --root /tmp/research-agent-phase1-acceptance
python3 src/memory.py init --root /tmp/research-agent-phase1-acceptance
```

结果：

- 第一次：新建目录数量 18，新建文件数量 3，已存在项目数量 0
- 第二次：新建目录数量 0，新建文件数量 0，已存在项目数量 21
- `.research-agent-root` 可被 `python3 -m json.tool` 解析
- `exports/index_manifest.json` 可被 `python3 -m json.tool` 解析

## add 验收结果

创建了 3 条基础记忆：

- `principle`：`memory/principles/principle-20260622-c0d7c855-代码最少原则.md`
- `context`：`memory/contexts/context-20260622-dd4a1ff3-研究生阶段.md`
- `project`：`memory/projects/project-20260622-33c089c6-科研记忆库项目.md`

三条命令均返回 0。

## validate 验收结果

首次验证结果：

```text
Validated: 3 files
Errors: 0
Warnings: 0
```

迁移后验证结果：

```text
Validated: 5 files
Errors: 0
Warnings: 0
```

## export 确定性结果

首次导出结果：

```text
Exported: 3 records
Skipped internal: 0
Skipped restricted: 0
JSONL records: 3
```

两次导出哈希完全一致：

```text
847f86c9e3564ef5ef90e582b1b4c1431cb7b558d989592504963c2f0c5ab5fd  /tmp/research-agent-phase1-acceptance/exports/memory.jsonl
d1212c7f0041a5a23e3f116caa98246962c967557690b8b66b50bc483c4218bc  /tmp/research-agent-phase1-acceptance/exports/index_manifest.json
```

说明：本机 `shasum` 在继承 `C.UTF-8` locale 时失败，因此哈希命令使用 `LC_ALL=C LANG=C shasum -a 256` 执行；导出命令本身返回 0。

## dry-run 文件哈希对比结果

执行 dry-run 前后分别记录所有数据文件哈希：

```bash
find /tmp/research-agent-phase1-acceptance -type f -exec shasum -a 256 {} \; | sort
```

随后执行：

```bash
cmp /tmp/phase1-before-dry-run.sha256 /tmp/phase1-after-dry-run.sha256
```

结果：

- `cmp` 返回 0
- dry-run 未修改任何数据文件

## context-transition 结果

正式执行情景迁移：

- 来源 context：`university-student`
- 目标 context：`industry-engineer`
- 生效日期：`2027-07-01`
- 来源文件更新为 historical：`memory/contexts/context-20260622-dd4a1ff3-研究生阶段.md`
- 新建 Context：`memory/contexts/context-20260622-8012f49e-企业研发阶段.md`
- 新建 Transition：`memory/transitions/context_transition-20260622-c9bdcd9e-从-研究生阶段-迁移到-企业研发阶段.md`

旧 Context 未删除，通过历史保留和上下文迁移表达情景变化。

## 迁移后文件数量

迁移后共有 5 个记忆文件：

- principle：1
- project：1
- context：2
- context_transition：1

文件列表：

```text
/tmp/research-agent-phase1-acceptance/memory/contexts/context-20260622-8012f49e-企业研发阶段.md
/tmp/research-agent-phase1-acceptance/memory/contexts/context-20260622-dd4a1ff3-研究生阶段.md
/tmp/research-agent-phase1-acceptance/memory/principles/principle-20260622-c0d7c855-代码最少原则.md
/tmp/research-agent-phase1-acceptance/memory/projects/project-20260622-33c089c6-科研记忆库项目.md
/tmp/research-agent-phase1-acceptance/memory/transitions/context_transition-20260622-c9bdcd9e-从-研究生阶段-迁移到-企业研发阶段.md
```

## internal 导出过滤结果

默认导出：

```text
Exported: 3 records
Skipped internal: 2
Skipped restricted: 0
Default JSONL records: 3
```

显式包含 internal：

```text
Exported: 5 records
Skipped internal: 0
Skipped restricted: 0
Include-internal JSONL records: 5
```

结果符合保密规则：默认跳过 `internal`，使用 `--include-internal` 后包含 `internal`，`restricted` 仍不导出。

## Git 变更范围

Task 1.8 只创建或修改以下文件：

- `README.md`
- `docs/phase_1_acceptance.md`
- `docs/PHASE_STATUS.json`

未修改源码、测试、Schema、模板、fixtures、`AGENTS.md`、`.gitignore`、`LICENSE` 或 `docs/ROADMAP.md`。

## 尚未完成内容

- 真实 iCloud 数据目录初始化尚未完成。
- 真实基础记忆录入尚未完成。
- Phase 2 SQLite FTS5 尚未实现。
- Phase 3 Chroma/Ollama 尚未实现。
- MCP 接口尚未实现。
- 文献自动化和总控 Agent 接入尚未实现。

## 结论

Phase 1 代码级验收通过；真实 iCloud 数据目录初始化和真实基础记忆录入尚未完成，因此 Phase 1 暂保持 active。
