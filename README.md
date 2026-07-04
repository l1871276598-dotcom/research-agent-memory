# Local Agent Operating System (LAOS)

LAOS 是一个本地优先、可审计、人工审核受控的 Agent 记忆与学习层。它把结构化记忆、项目上下文、任务结果、反思、策略候选和可复用原则统一到本地文件与可重建索引中，供 GPT、Codex、Claude、本地模型和其他 Agent 复用。

## 当前版本

- 开发版本：`v0.9.0-development`
- 目标发布版本：`v0.9.0`
- SQLite schema：`v3`
- Python：`3.11+`
- 主要验证平台：macOS
- 运行边界：本地、可信操作者、命令行

`v0.9.0` 尚未在本仓库中打 tag、合并或正式发布。当前分支通过 Stage 16 发布审查后，可推送并创建 Draft PR；合并、tag 和正式 release 仍需单独确认。

## v0.9 已实现能力

- Markdown / JSONL 权威数据源与可重建 SQLite FTS5 索引
- ChatGPT 官方 ZIP 手动导入与手动文件归档
- 结构化记忆、workspace / project / confidentiality / 时间有效性
- candidate-only 创建与统一 Review Gate
- restricted 内容默认不进入搜索、Context Pack 或外部调用上下文
- 九 Agent 精确注册表与只负责路由的 Orchestrator
- 规范 JSON CLI：`src/laos.py`
- 幂等 Loop run v2 合约与旧 v1 / 早期 v2 兼容读取
- 确定性 Reflection Agent
- 确定性 Policy Agent：精确去重、显式相反指令冲突检测
- 固定三条独立证据阈值的低风险 principle candidate 生成
- workspace / project 分区证据聚合
- 轻量 Loop Coordinator：

```text
finalize
→ reflect
→ suggest policies
→ evaluate low-risk candidate generation
```

- 本地运行产物路径、符号链接、证据链和 Review Gate 安全检查
- GitHub Actions 与本地完整回归验证

## 安全边界

LAOS v0.9 保持以下硬边界：

1. Agent 只能创建 `candidate`，不能直接写入 `active` memory。
2. `active` 只能通过 Review Gate 产生。
3. Reflection、Policy、Candidate 和 Coordinator Agent 都不会自动接受候选。
4. Coordinator 不执行任务、不后台监听、不自动重试。
5. Policy Agent 不自动修改提示词、代码、Agent 行为或 `memory_rules.md`。
6. 三条证据必须来自不同的 task/result 指纹，并且属于同一 workspace 与 project 分区。
7. 真实记忆、数据库、PDF、日志、缓存、凭据和受限资料不得提交到 GitHub。

当前未实现操作者身份认证、owner/agent 身份认证和多用户授权，因此不要把 `src/laos.py` 暴露为不受信任的 HTTP、MCP、多用户或无人值守服务。

## 不属于 v0.9 的能力

- 后台 watcher 或常驻自动更新服务
- 自主任务执行与自动重试
- 自动 policy approval
- 自动 candidate accept 或 active-memory promotion
- 语义冲突自动解决
- 向量数据库与真实 embedding 检索
- MCP 服务
- GUI、Web 或桌面应用
- 大型自主 Coordinator / Meta Planner
- 内置文献管理系统扩展

文献、Zotero、EndNote、网页和其他外部来源后续只通过 Adapter 或外部接口接入，不进入 Memory Core 主线。

## 数据与状态目录

权威数据目录由用户指定，例如：

```text
ResearchAgent/
├── memory/
├── imports/
├── manuscripts/
├── exports/
└── backups/
```

本地派生状态目录保存 SQLite、Loop artifacts、缓存和日志，例如：

```text
~/Library/Application Support/ResearchAgent/
├── memory.sqlite
└── loop_engineering/
    ├── runs/
    └── generated_candidates/
```

SQLite、WAL/SHM、缓存和 Loop runtime artifacts 不应放入 iCloud 数据目录，也不应提交到 GitHub。

## 快速开始

```bash
REPO_ROOT="/path/to/research-agent-memory"
DATA_ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent"
STATE_DIR="$HOME/Library/Application Support/ResearchAgent"

cd "$REPO_ROOT"
```

初始化数据目录和 SQLite：

```bash
python3 src/memory.py init --root "$DATA_ROOT"
python3 src/memory.py db-init \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
```

验证、索引、搜索和健康检查：

```bash
python3 src/memory.py validate --root "$DATA_ROOT"
python3 src/memory.py index --root "$DATA_ROOT" --state-dir "$STATE_DIR"
python3 src/memory.py search "代码最少" --root "$DATA_ROOT" --state-dir "$STATE_DIR"
python3 src/memory.py doctor --root "$DATA_ROOT" --state-dir "$STATE_DIR"
```

## LAOS JSON CLI

CLI 接受 `--task-json` 或 UTF-8 `--task-file`，成功时输出单行规范 JSON，失败时输出安全错误 JSON 并返回非零退出码。

### 创建候选记忆

```bash
python3 src/laos.py \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-json '{"type":"memory.create","input":{"type":"principle","title":"最少代码","scope":"global","workspace":"personal","confidentiality":"personal","source":"manual:user_confirmed","confidence":"confirmed","content":"使用尽可能少的代码实现相同功能。"}}'
```

该命令只创建 `candidate`。记录输出中的 `candidate_id` 后，使用 Review Gate 显式审核：

```bash
python3 src/laos.py \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-json '{"type":"memory.review","workspace":"personal","input":{"action":"accept","candidate_id":"CANDIDATE_ID"}}'
```

work candidate 必须显式使用顶层 `"workspace":"work"`。workspace 或 project 不匹配时审核失败。

### 搜索和 Context Pack

```bash
python3 src/laos.py \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-json '{"type":"memory.search","input":{"query":"最少代码","workspace":"personal"}}'
```

```bash
python3 src/laos.py \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-json '{"type":"context.build","input":{"query":"最少代码","workspace":"personal"}}'
```

## v0.9 最终 Loop Coordinator 示例

`loop.coordinate` 接收的是已经完成的任务证据；它不会执行 JSON 中描述的任务。

创建任务文件：

```bash
cat > /tmp/laos-loop-task.json <<'JSON'
{
  "type": "loop.coordinate",
  "input": {
    "task": "验证迁移流程",
    "result": "迁移在写入前因版本不匹配而停止",
    "outcome": "fail",
    "error": "目标版本不匹配",
    "reflection": "预检查成功阻止了不安全写入",
    "root_cause": "流程缺少目标版本预检查",
    "next_change": "迁移前必须验证目标版本",
    "workspace": "personal"
  }
}
JSON
```

运行 Coordinator：

```bash
python3 src/laos.py \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --task-file /tmp/laos-loop-task.json
```

预期行为：

- 创建或复用一个幂等 Loop run
- 创建一个待审核 session candidate（结果非空时）
- 生成 `reflection_result.json`
- 生成 `policy_candidates.json` 与 `policy_review.md`
- 评估相同策略在当前 workspace/project 中的独立证据数量
- 单次证据不会生成 principle candidate
- 同一策略达到三条不同 task/result 指纹后，才生成一个待 Review Gate 审核的 principle candidate
- 输出保持 `requires_review: true` 和 `applied: false`

重复完全相同的请求会复用同一 run，不会增加独立证据。要达到三条证据阈值，必须来自真实不同的任务与结果证据。

Coordinator 允许的必填字段：

- `task`
- `result`
- `outcome`: `pass` 或 `fail`
- `workspace`: `personal` 或 `work`

可选字段：

- `error`
- `reflection`
- `root_cause`
- `next_change`
- `project`

任何 approval、activation 或阈值覆盖字段都会被拒绝。

## Loop artifacts

每个 run 位于：

```text
<state-dir>/loop_engineering/runs/<run_id>/
```

可能包含：

```text
run.json
reflection.md
policy_suggestions.md
reflection_result.json
policy_candidates.json
policy_review.md
```

低风险 candidate generation 状态位于：

```text
<state-dir>/loop_engineering/generated_candidates/<request_id>.json
```

这些文件是本地运行与审计产物，不是 active memory。

## 导入

ChatGPT 官方 ZIP 仅支持用户手动下载后本地导入：

```bash
python3 src/memory_tools.py import-chatgpt \
  --zip "$HOME/Downloads/chatgpt-export.zip" \
  --root "$DATA_ROOT" \
  --dry-run

python3 src/memory_tools.py import-chatgpt \
  --zip "$HOME/Downloads/chatgpt-export.zip" \
  --root "$DATA_ROOT"
```

手动文件导入：

```bash
python3 src/memory_tools.py import-manual \
  --path "$HOME/Downloads/note.txt" \
  --root "$DATA_ROOT" \
  --dry-run

python3 src/memory_tools.py import-manual \
  --path "$HOME/Downloads/note.txt" \
  --root "$DATA_ROOT"
```

PDF/DOCX 等二进制文件只归档 raw；当前不会进行深度结构化解析或全文索引。

## 本地验证

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q src
git diff --check
git diff --cached --check
```

Stage 16 发布审查基线：

```text
364 tests passed
compileall passed
git diff --check passed
git diff --cached --check passed
```

远程分支尚未触发或完成 GitHub Actions 时，不应声称远程 CI 已通过。

## 发布门禁

v0.9 的发布顺序是：

```text
Stage 16 release review
→ local commit
→ push feature branch
→ Draft PR
→ remote CI
→ human review
→ merge confirmation
→ v0.9.0 tag / GitHub release confirmation
```

Draft PR、合并、tag 和正式 release 是不同 Gate。创建 Draft PR 不等于已经发布。

## 文档

- 当前阶段状态：`docs/PHASE_STATUS.json`
- v0.9 架构与安全审查：`docs/progress/2026-07-04-stage-15-v09-architecture-security-audit.md`
- v0.9 发布审查：`docs/progress/2026-07-04-stage-16-v09-release-review.md`
- Trusted Memory Loop：`docs/TRUSTED_MEMORY_LOOP.md`
- Schema：`schemas/`

## 许可证

MIT License。
