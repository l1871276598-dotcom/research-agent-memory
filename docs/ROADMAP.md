# 项目路线图和阶段门禁

## 架构边界

1. GitHub 只保存代码、规则、Schema、模板、测试和示例配置。
2. iCloud 保存 Markdown、JSONL、文献笔记、文献矩阵和允许同步的文件。
3. 活动 SQLite、Chroma、缓存和日志只保存在 Mac 本地运行目录。
4. 真实个人记忆、内部资料、受限数据、数据库和 PDF 不进入 GitHub。
5. 每个阶段必须独立验收，不能一次实现多个阶段。
6. 后一阶段不能破坏前一阶段的独立可用性。
7. 记忆模型支持 context、context_transition、时间有效性、workspace 和 confidentiality。
8. 情景变化采用历史保留和上下文迁移，不删除旧记忆。
9. personal 与 work 工作区默认隔离。
10. inferred 信息不得覆盖 confirmed 信息。

## 当前发布范围

- 当前版本为 `v0.9.0-development`，目标发布版本为 `v0.9.0`。
- 当前实现、集成、验收和发布范围仅承诺 macOS 与 Python 3.11 或更高版本；Linux 和 Windows 兼容性后置到独立阶段。
- 文献系统不再作为内置路线阶段推进；未来如有需要，只通过 External Adapter 接口接入。
- 当前路线不把大型自主 Agent、完整 Meta Planner 或多 Agent 自主协商作为交付目标。
- 规则型协调器只负责受约束推进，不直接写 active memory，不绕过 Review Gate。

## 当前 Gate：Stage 17 macOS 最终整合审查

当前 HEAD 的本地 macOS 整合验收已通过，证据记录在：

```text
docs/progress/2026-07-05-stage-17-macos-final-integration-review.md
```

当前 Gate 状态：

- 508 项本地测试通过。
- compileall 与 Git diff 检查通过。
- Critical、Major 和 Minor 发现均为 0。
- 远程 macOS CI 尚未运行。
- 当前分支尚未推送，Draft PR 尚未创建。
- 合并、tag、GitHub Release 和部署仍需后续人工 Gate。

macOS-first 决策见：

```text
docs/decisions/2026-07-05-macos-first-delivery-scope.md
```

## Phase 0：仓库初始化和执行约束

### 阶段目标

建立最小仓库结构、忽略规则、执行约束和阶段门禁，使后续阶段有清晰边界。

### 输入

- GitHub 仓库地址
- 本地项目目录
- 仓库执行规则

### 输出

- Git 初始化状态
- `.gitignore`
- `AGENTS.md`
- `docs/ROADMAP.md`
- `docs/PHASE_STATUS.json`

### 包含功能

- 仓库连接到 GitHub 远端
- 定义禁止提交的数据类型
- 定义任务执行规则
- 定义阶段路线图和状态门禁

### 明确不包含的功能

- 记忆数据结构实现
- 检索实现
- MCP 接口
- 文献自动化
- Agent 总控逻辑

### 验收条件

- 仓库已初始化并设置远端地址
- `.gitignore` 覆盖密钥、数据库、PDF、缓存和真实记忆文件
- `AGENTS.md` 明确执行约束
- 路线图和阶段状态文件存在且内容完整
- 未创建业务代码或提前实现后续阶段

### 阶段版本标签

v0.0-repository-bootstrap

### 解锁下一阶段的条件

- Phase 0 验收完成
- Phase 1 状态设置为 unlocked

## Phase 1：文件型基础记忆库

### 阶段目标

建立以 Markdown 和 JSONL 为权威数据源的基础记忆库，不依赖数据库即可读写和审计。

### 输入

- Phase 0 执行约束
- 记忆字段需求
- workspace 与 confidentiality 隔离规则

### 输出

- 文件型记忆格式
- 示例配置或模板
- 基础读写和校验规则
- Phase 1 验收文档

### 包含功能

- Markdown 记忆说明结构
- JSONL 记忆记录结构
- context、context_transition、时间有效性、workspace 和 confidentiality 字段约定
- confirmed 与 inferred 信息的保留规则
- personal 与 work 工作区默认隔离规则

### 明确不包含的功能

- SQLite FTS5 检索
- Chroma 向量索引
- Ollama 嵌入
- MCP 服务
- 文献下载或解析
- Agent 调度

### 验收条件

- Markdown 和 JSONL 可作为独立可读的数据源
- 不需要数据库即可审计记忆内容
- 情景变化通过历史保留和上下文迁移表达
- inferred 信息不会覆盖 confirmed 信息
- 不写入 GitHub 禁止保存的真实数据

### 阶段版本标签

v0.1-memory-files

### 解锁下一阶段的条件

- Phase 1 独立验收完成
- 文件型记忆库在无数据库状态下仍可使用

## Phase 2：SQLite FTS5 精确检索

### 阶段目标

在不替代权威文件数据源的前提下，提供可删除后重建的 SQLite FTS5 精确检索索引。

### 输入

- Phase 1 的 Markdown 和 JSONL 数据源
- 本地运行目录
- 检索字段定义

### 输出

- SQLite FTS5 索引
- 索引构建命令
- 精确检索命令或模块
- Phase 2 验收文档

### 包含功能

- 从 Markdown 和 JSONL 重建 SQLite 索引
- 本地运行目录中的活动 SQLite 文件
- 基于关键词和字段的精确检索
- 索引删除后重建验证

### 明确不包含的功能

- 语义向量检索
- Chroma 持久化
- Ollama 嵌入
- MCP 服务
- 文献自动化
- Agent 总控逻辑

### 验收条件

- SQLite 文件不位于 iCloud 数据目录
- 删除 SQLite 后可以从权威数据源重建
- Phase 1 文件型记忆库仍可独立使用
- 检索失败时命令返回非零退出码

### 阶段版本标签

v0.2-fts-search

### 解锁下一阶段的条件

- Phase 2 独立验收完成
- SQLite 精确检索不破坏 Phase 1 文件数据源

## Phase 3：Ollama 和 Chroma 语义检索

### 阶段目标

在本地运行目录中加入可删除后重建的语义索引，支持基于 Ollama 和 Chroma 的语义检索。

### 输入

- Phase 1 的权威数据源
- Phase 2 的精确检索能力
- 本地 Ollama 模型
- 本地运行目录

### 输出

- Chroma 语义索引
- 嵌入生成流程
- 语义检索命令或模块
- Phase 3 验收文档

### 包含功能

- 从权威文件重建 Chroma 索引
- 使用 Ollama 生成本地嵌入
- 语义检索与精确检索并存
- Chroma 数据仅保存在 Mac 本地运行目录

### 明确不包含的功能

- 云向量数据库
- 付费 API
- MCP 统一接口
- 文献自动化
- Agent 总控逻辑

### 验收条件

- Chroma 索引可删除后重建
- 活动 Chroma 数据不进入 iCloud 和 GitHub
- Phase 1 和 Phase 2 仍可独立使用
- 不引入未明确要求的云服务或付费 API

### 阶段版本标签

v0.3-semantic-search

### 解锁下一阶段的条件

- Phase 3 独立验收完成
- 精确检索和语义检索都可从权威文件重建

## Phase 4：MCP 统一接口

### 阶段目标

为文件记忆库、精确检索和语义检索提供统一 MCP 接口，同时保持底层数据源独立可用。

### 输入

- Phase 1 文件型记忆库
- Phase 2 SQLite FTS5 检索
- Phase 3 Chroma 语义检索
- MCP 工具边界

### 输出

- MCP 服务或工具定义
- 记忆读写接口
- 检索接口
- Phase 4 验收文档

### 包含功能

- 统一读取记忆
- 统一追加记忆
- 精确检索入口
- 语义检索入口
- workspace 和 confidentiality 访问边界

### 明确不包含的功能

- GUI 或 Web 前端
- 文献自动下载
- 论文工作流总控 Agent
- 云端数据库
- 付费 API

### 验收条件

- MCP 接口不会绕过文件型权威数据源
- personal 与 work 默认隔离
- 受限数据不会跨 workspace 泄露
- Phase 1 到 Phase 3 在没有 MCP 时仍可独立使用

### 阶段版本标签

v0.4-mcp-memory

### 解锁下一阶段的条件

- Phase 4 独立验收完成
- MCP 接口通过边界和隔离检查

## Phase 5：外部文献接口预留（不作为当前内置范围）

### 阶段目标

保留未来文献、期刊和 PDF 资料接入的外部接口边界，但不在当前版本内置文献系统。

### 输入

- Phase 4 MCP 接口
- 外部 Adapter 约束
- 数据隔离规则

### 输出

- External Adapter 接口约束
- 文献类数据隔离规则
- Phase 5 验收文档

### 包含功能

- 外部文献接口保留位
- PDF 与受限资料的排除规则
- 与 Memory Core 解耦的 Adapter 边界

### 明确不包含的功能

- 内置文献数据库
- 将 PDF 提交到 GitHub
- 将内部资料提交到 GitHub
- 云数据库
- 付费 API
- 大型论文工作流总控 Agent

### 验收条件

- 只保留外部接口，不引入新的内置文献数据面
- PDF、内部资料和受限数据不进入 GitHub
- Phase 1 到 Phase 4 仍可独立使用
- 后续如接入文献能力，也不能覆盖已确认记忆

### 阶段版本标签

v0.5-external-literature-interface

### 解锁下一阶段的条件

- Phase 5 边界文档完成
- 外部接口方案遵守数据隔离和 GitHub 排除规则

## Phase 6：规则型协调器与外部适配器集成

### 阶段目标

在前述阶段独立可用后，扩展规则型协调器与外部适配器的受约束集成，不引入大型自主 Planner。

### 输入

- Phase 5 外部接口边界
- 记忆、检索和 MCP 接口
- 数据隔离策略

### 输出

- 外部适配器接入规则
- 规则型协调器边界
- 任务编排策略
- Phase 6 验收文档

### 包含功能

- 基于规则的任务路由
- 外部适配器与记忆库连接
- 根据 workspace 和 confidentiality 控制访问
- 保留历史上下文并迁移情景变化

### 明确不包含的功能

- 不受控的自主执行
- 覆盖 confirmed 记忆
- 跨 workspace 混用个人和工作数据
- 大型自主 Planner 或 Meta Planner
- 未明确要求的 LangChain、LangGraph、Mem0、云数据库或付费 API
- GUI、Web 前端或桌面应用

### 验收条件

- Agent 只在明确规则范围内编排任务
- personal 与 work 默认隔离
- inferred 信息不会覆盖 confirmed 信息
- 后一阶段能力不破坏前面阶段的独立可用性
- 真实个人记忆、内部资料、受限数据、数据库和 PDF 不进入 GitHub

### 阶段版本标签

v0.6-agent-integration

### 解锁下一阶段的条件

- Phase 6 为当前路线图最后阶段；完成后仅允许在明确新任务中扩展路线图。
