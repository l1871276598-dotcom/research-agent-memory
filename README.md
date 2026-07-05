# Local Agent Operating System (LAOS)

LAOS 是一个本地优先、可审计、人工审核受控的 Agent 记忆与学习层。它把结构化记忆、项目上下文、任务结果、反思、策略候选、会话审查和可复用原则统一到本地文件与可重建索引中，供 GPT、Codex、Claude、本地模型和其他 Agent 复用。

## 当前版本

- 开发版本：`v0.9.0-development`
- 目标发布版本：`v0.9.0`
- SQLite schema：`v3`
- Python：`3.11+`
- 当前验收平台：macOS
- 后置平台：Linux、Windows
- 运行边界：本地、可信操作者、命令行或受控本地服务

当前实现、集成、验收和发布范围仅承诺 macOS。Linux 与 Windows 兼容性将在 macOS 功能完整性和集成验收完成后作为独立阶段处理，不作为当前交付门禁。

`v0.9.0` 尚未打 tag、合并或正式发布。当前本地分支尚未推送；Draft PR、远程 CI、合并、tag、GitHub Release 和部署仍需分别通过后续 Gate。

## v0.9 已实现能力

### Memory Core 与安全边界

- Markdown / JSONL 权威数据源与可重建 SQLite FTS5 索引
- ChatGPT 官方 ZIP 手动导入与手动文件归档
- 结构化记忆、workspace / project / confidentiality / 时间有效性
- candidate-only 创建与统一 Review Gate
- restricted 内容默认不进入搜索、Context Pack 或外部调用上下文
- Trusted Memory Loop、写前校验、事务回滚、重建索引和 durable journal

### 统一 11 Agent JSON CLI

`src/laos.py` 通过一个精确注册表提供：

- Import Agent
- Memory Agent
- Search Agent
- Review Agent
- Context Agent
- Deterministic Reflection Agent
- Policy Agent
- Low-risk Candidate Agent
- Loop Coordinator Agent
- Conversation Review Agent
- Reflection Record Agent

Orchestrator 只负责上下文准备、精确路由和结果校验，不承载审核或持久化业务逻辑。

### 确定性学习链

```text
finalize
→ loop.reflect
→ loop.suggest-policies
→ loop.generate-candidate
→ Review Gate
```

已实现：

- 幂等 Loop run v2 合约
- v1 与早期 v2 兼容读取
- 结构化 `root_cause` 与 `next_change` 证据
- 确定性 Reflection artifact
- Policy 精确去重和显式相反指令冲突检测
- 固定三条独立 task/result 指纹阈值
- workspace / project 分区证据聚合
- 两阶段 candidate recovery
- 轻量 `loop.coordinate` 编排

### 会话反思与模型后端

- Conversation Review prepare / apply
- 周期性 `reflection.record`
- 可插拔 ModelBackend registry
- Codex backend
- OpenAI-compatible backend
- Review state、procedure proposal 和 session 状态

### Runtime、Bridge 与 MCP checkpoint

- Agent Runtime、Tool Registry 和 SessionStore
- Procedure 生命周期与 curator 流程
- Bridge Event Inbox、projector 和 crash recovery
- MCP stdio 与回环 Streamable HTTP 服务
- 显式 `laos_capture_checkpoint` 工具及验证流程
- 只读、分区受控的 `memory_search` 工具
- 自动更新 Loop 的真实 baseline → review → verification → comparison 验收入口
- `tools/developer_bridge_adapter.py` 提供固定配置、固定作用域的 checkpoint 捕获、session 搜索和 session 读取入口

Developer Bridge Adapter 不接受任意路径、命令或环境注入；data、state、code 路径必须相互隔离，输入输出有固定大小边界，越权、冲突和不安全路径按 fail-closed 处理。

MCP checkpoint 是显式工具通道，不是浏览器侧被动、无损或自动对话采集。真实 ChatGPT 五轮写入验收仍受当前账户能力限制。

## 硬性安全边界

1. Agent 只能创建 `candidate`，不能直接写入 `active` memory。
2. `active` 只能通过 Review Gate 产生。
3. Reflection、Policy、Candidate、Coordinator 和会话审查流程都不会自动接受候选。
4. `loop.coordinate` 不执行任务、不后台监听、不自动重试。
5. Policy Agent 不自动修改提示词、代码、Agent 行为或 `memory_rules.md`。
6. 三条证据必须来自不同 task/result 指纹，并属于同一 workspace 与 project 分区。
7. MCP、HTTP 和 Bridge 服务只适用于本地可信操作者；当前没有多用户认证和能力授权。
8. 真实记忆、数据库、PDF、日志、缓存、凭据和受限资料不得提交到 GitHub。

## 不属于 v0.9 的能力

- 浏览器侧被动、无损 ChatGPT 对话采集
- 自动 policy approval
- 自动 candidate accept 或 active-memory promotion
- 无人值守后台重试或自主任务执行
- 语义冲突自动解决
- 真实向量数据库和 embedding 检索
- 多用户认证与 owner/agent 授权
- GUI、Web 前端或桌面应用
- 大型自主 Coordinator / Meta Planner
- 内置文献管理系统扩展
- 当前阶段的 Linux 与 Windows 发布承诺

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

本地派生状态目录例如：

```text
~/Library/Application Support/ResearchAgent/
├── memory.sqlite
├── sessions.sqlite
├── bridge_events.sqlite
├── review_state.sqlite
└── loop_engineering/
    ├── runs/
    └── generated_candidates/
```

SQLite、WAL/SHM、缓存、日志和 Loop runtime artifacts 不应放入 iCloud 数据目录，也不应提交到 GitHub。

## 快速开始

```bash
REPO_ROOT="/path/to/research-agent-memory"
DATA_ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/ResearchAgent"
STATE_DIR="$HOME/Library/Application Support/ResearchAgent"

cd "$REPO_ROOT"
```

推荐本地初始化：

```bash
python3 tools/setup_local.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --profile personal \
  --workspace personal
```

也可以手动初始化核心数据与索引：

```bash
python3 src/memory.py init --root "$DATA_ROOT"
python3 src/memory.py db-init \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR"
python3 src/memory.py validate --root "$DATA_ROOT"
python3 src/memory.py index --root "$DATA_ROOT" --state-dir "$STATE_DIR"
python3 src/memory.py doctor --root "$DATA_ROOT" --state-dir "$STATE_DIR"
```

## LAOS JSON CLI

CLI 接受 `--task-json` 或 UTF-8 `--task-file`。成功时输出单行规范 JSON；失败时输出安全错误 JSON 并返回非零退出码。

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

## 最终 `loop.coordinate` 示例

`loop.coordinate` 接收已经完成的任务证据；它不会执行 JSON 中描述的任务。

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
- 评估当前 workspace/project 中相同策略的独立证据数量
- 单次证据不会生成 principle candidate
- 三条不同 task/result 指纹后才生成待审核 principle candidate
- 输出保持 `requires_review: true` 和 `applied: false`

重复完全相同的请求会复用同一 run，不会增加独立证据。

必填字段：

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

## 会话反思任务

`reflection.prepare` 和 `reflection.apply` 用于显式会话审查；`reflection.record` 用于受控周期记录。通过 `--model-config` 可选择 OpenAI-compatible backend；未指定时使用默认 Codex backend。

```bash
python3 src/laos.py \
  --root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --model-config config/model_backend.example.json \
  --task-json '{"type":"reflection.record","workspace":"personal","input":{"session_id":"session-1","messages":[{"role":"user","content":"记录代码最少原则"}]}}'
```

生成的长期候选仍必须经过 Review Gate。

## MCP checkpoint 验证

```bash
python3 -m pip install -r requirements-mcp.txt
python3 tools/mcp_checkpoint_trial.py prepare \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --workspace personal
python3 tools/mcp_checkpoint_trial.py serve --state-dir "$STATE_DIR"
```

详细流程见 `docs/mcp_checkpoint_validation.md`。该流程不等同于浏览器侧自动对话采集。

## 自动更新 Loop 真实验收入口

```bash
python3 tools/learning_loop.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --work-root "$REPO_ROOT" \
  advance \
  --task-file config/stage07_2_real_acceptance_baseline.example.json
```

```bash
python3 tools/learning_loop.py \
  --data-root "$DATA_ROOT" \
  --state-dir "$STATE_DIR" \
  --work-root "$REPO_ROOT" \
  auto-status \
  --run-id stage07-2-work-baseline
```

人工接受或拒绝候选仍通过显式 `review` 命令完成；自动协调器不会替代审核者。

完整流程见 `docs/stage_07_2_real_loop_acceptance.md`。

## Loop artifacts

每个确定性学习 run 位于：

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
python3 -m compileall -q src tests
git diff --check
git diff --cached --check
```

当前 HEAD 的本地 macOS 集成结果记录在 `docs/progress/2026-07-05-stage-17-macos-final-integration-review.md`。远程 macOS CI 未完成时，不应声称远程检查全绿或版本已发布。

## 发布门禁

```text
Stage 17 local macOS integration review
→ commit documentation alignment
→ push feature branch
→ Draft PR macOS CI
→ human review
→ merge confirmation
→ v0.9.0 tag / GitHub Release confirmation
```

本地验收、Draft PR、远程 CI、合并、tag 和正式 release 是不同 Gate。任何前置 Gate 通过都不代表后续 Gate 自动通过。

## 文档

- 当前阶段状态：`docs/PHASE_STATUS.json`
- 当前路线图：`docs/ROADMAP.md`
- macOS-first 决策：`docs/decisions/2026-07-05-macos-first-delivery-scope.md`
- v0.9 架构与安全审查：`docs/progress/2026-07-04-stage-15-v09-architecture-security-audit.md`
- v0.9 初始发布审查：`docs/progress/2026-07-04-stage-16-v09-release-review.md`
- 当前 HEAD macOS 最终整合审查：`docs/progress/2026-07-05-stage-17-macos-final-integration-review.md`
- MCP checkpoint：`docs/mcp_checkpoint_validation.md`
- 自动更新 Loop：`docs/stage_07_2_real_loop_acceptance.md`
- Trusted Memory Loop：`docs/TRUSTED_MEMORY_LOOP.md`
- Schema：`schemas/`

## 许可证

MIT License。
