# LAOS v0.8 阶段进度：CLI 与完整管线

日期：2026-06-30
阶段状态：已完成

## 本阶段修改文件

- `src/laos.py`
- `tests/test_laos.py`
- `README.md`
- `docs/progress/2026-06-30-stage-06-cli-pipeline.md`

## 已实现

- `build_application(root, state_dir)` 已组装 Memory Store、Candidate Store、Memory Core、Review Gate、Context Builder、五个 Agents、Registry 和 Orchestrator。
- CLI 支持 `--root`、可选 `--state-dir` 以及互斥的 `--task-json` / `--task-file`。
- 成功时 stdout 输出单行 canonical JSON。
- 失败时 stderr 输出单行安全 JSON，包含稳定的 `error.code` 和 `error.message`，并返回非零退出码。
- `--task-file` 同时兼容普通 UTF-8 和带 BOM 的 UTF-8 JSON。
- 端到端管线已证明：`memory.create` 只创建 candidate，显式 `memory.review` accept 后才成为 active，然后可被 search 和 context 召回。
- README 已增加 create、review、search 和 context 命令，并明确不会自动 accept、restricted 不会进入 context。

## 检查与测试

- Pipeline 聚焦测试：5 项通过。
- 完整回归：298 项通过，0 失败。
- `python3 -m compileall -q src tests`：通过。
- `git diff --check`：通过。
- 规格子代理复核：APPROVED。
- 代码质量子代理复核：APPROVED。

## LAOS v0.8 当前总体进度

已完成 Task 1–6：

1. Base Agent 与 Agent Registry。
2. 纯路由 Orchestrator。
3. Memory lifecycle 与 Agent adapters。
4. Review Gate 单一 active memory 写入通道。
5. 最小、确定、受限安全的 Context Builder。
6. CLI composition root 与完整 candidate → review → active 管线。

## 未完成

1. 架构与单一 active 写入权限的全量静态审计。
2. 敏感产物、同步目录状态、语法、diff 和全部需求的逐项证据审计。
3. 最终全量验证与总 Markdown 完成报告。

## 其他

- 未执行 Git commit 或 push。
