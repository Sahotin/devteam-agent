# DevTeam Agent v0.7 实现说明

## 1. 本版本目标

v0.7 在 v0.6 代码 Hybrid RAG 的基础上完成分层 Memory 子系统，使 Agent 不再只依赖当前请求和代码片段，而能安全地复用当前任务上下文、项目事实和历史工程经验。

本版本重点不是简单增加一张“聊天记录表”，而是实现记忆从产生、治理、检索、引用到失效的完整生命周期。

## 2. 分层设计

### 2.1 Short-term Memory

Short-term Memory 绑定具体任务。编排器在保存 PRD、Architecture、CodeChange、Review、TestReport 和 GitCommit Artifact 后同步生成阶段记忆。

它用于向后续 Agent 提供当前任务已确认的事实，避免重复依赖不断增长的完整对话。任务到达 `COMPLETED` 后，短期记忆统一标记为 `STALE`，结构化 Artifact 仍作为审计事实保留。

### 2.2 Project Memory

Project Memory 保存项目级且已经验证的稳定知识。当前自动沉淀来源是完成任务中的最新 Architecture Artifact，每条 ADR 独立成为一条项目记忆，包含决策、依据和权衡。

### 2.3 Long-term Memory

Long-term Memory 保存可跨任务复用的工程经验。当前自动沉淀两类内容：

- 最终已完成任务中曾出现的阻断级 Review 问题与修复建议。
- 最终已完成任务中曾出现的测试失败模式。

只有任务最终完成，相关经验才被视为已经经过后续质量门禁验证，避免把尚未证明有效的 Agent 推断直接写入长期记忆。

## 3. 记忆数据模型

每条 Memory 包含以下关键字段：

- `project_id` 与可选 `task_id`：实现项目及任务作用域隔离。
- `type`：`SHORT_TERM`、`PROJECT` 或 `LONG_TERM`。
- `status`：`CANDIDATE`、`VERIFIED`、`STALE` 或 `REJECTED`。
- `category`、`summary`、`content`：支持分类、展示与检索。
- `source_type`、`source_id`、`source_revision`：记录来源及代码版本。
- `confidence`：表示可信度，用于检索排序。
- `metadata`：保存 ADR、问题编号和冲突键等扩展信息。
- `fingerprint`：规范化内容的 SHA-256 指纹，用于幂等去重。
- `embedding`：离线 Hash Embedding 向量。

数据库通过 `(project_id, fingerprint)` 唯一约束保证并发条件下不会在同一项目重复写入相同记忆。Short-term 指纹额外包含任务 ID，防止不同任务的阶段上下文错误合并。

## 4. 写入治理

### 4.1 敏感信息脱敏

摘要、正文和 Metadata 中的字符串在持久化前统一处理。当前覆盖密码、Token、Secret、API Key、Bearer Token 和 PEM 私钥块。工具审计结果不保存完整记忆正文。

### 4.2 去重

系统对脱敏后的内容执行小写化和空白规范化，再计算指纹。重复写入返回已有记录，并更新更高可信度、来源版本及补充 Metadata，保证任务重试和重复沉淀是幂等的。

### 4.3 冲突处理

需要唯一性的项目事实可在 Metadata 中声明 `conflict_key`。当相同类型、分类和冲突键下已经存在不同内容的 `VERIFIED` 记忆时，新事实会被降级为 `CANDIDATE`，可信度最高为 0.6，并记录 `conflicts_with`。系统不静默覆盖旧事实，必须通过状态接口人工确认。

### 4.4 版本失效

Project 和 Long-term Memory 可以绑定代码修订号。调用版本对账接口后，来源修订号不同于当前版本的有效记忆会被标记为 `STALE`，默认检索不再返回。

## 5. 混合检索

Memory Search 的检索流程如下：

1. 根据项目、类型、状态和分类过滤候选集。
2. Short-term Memory 只允许当前任务读取，未提供任务 ID 时完全排除。
3. 使用 BM25 计算词法相关性。
4. 使用 256 维 Hash Embedding 计算向量相似度。
5. 使用 Reciprocal Rank Fusion 合并两类排名。
6. 使用可信度、`VERIFIED` 状态和分类命中进行小幅加权。
7. 返回 Top-K 结果及分项分数，便于调试与评估。

Hash Embedding 是无外部服务依赖、结果确定的 V1 基线，适合单元测试和离线演示，但不等价于真实语义 Embedding。后续可以通过现有 `EmbeddingProvider` 接口替换为生产模型，无需修改 Memory 领域模型。

## 6. Agent 集成

- Product Agent：编排器根据原始需求检索项目及长期记忆，并将结果注入 PRD 输入。
- Architect Agent：根据 PRD 标题和需求检索当前任务、项目及长期记忆。
- Developer Agent：先调用代码 `rag.search`，再调用受权限控制的 `memory.search`，随后执行精确代码搜索和文件工具。
- Reviewer Agent：在读取真实变更文件前检索相关工程约定及历史问题。
- Tester Agent：在生成测试计划前检索当前任务事实与历史测试经验。

PRD、Architecture、CodeChange、Review 和 TestReport Artifact 均保存 `memory_ids`，可以回答“Agent 做出这个结论时具体使用了哪些记忆”。工具调用同时写入 ToolCall 审计表。

## 7. 编排器集成

编排器承担两项 Memory 生命周期职责：

- 每次 Artifact 与 Checkpoint 成功保存后，生成一条 Short-term Memory。
- 任务进入 `COMPLETED` 后，将最新架构决策及已经验证的失败经验沉淀为 Project/Long-term Memory，再令短期记忆失效。

Git Commit 成功后会再次执行幂等沉淀，使已有项目记忆的 `source_revision` 更新为真实 Commit SHA。

## 8. API 与工具

新增主要接口：

- `POST /api/v1/projects/{project_id}/memories`
- `GET /api/v1/projects/{project_id}/memories`
- `POST /api/v1/projects/{project_id}/memories/search`
- `PATCH /api/v1/memories/{memory_id}/status`
- `POST /api/v1/projects/{project_id}/memories/reconcile-revision`
- `GET /api/v1/tasks/{task_id}/short-term-memory`
- `POST /api/v1/tasks/{task_id}/memory/consolidate`

新增 `memory.search` Tool，权限为 `memory:search`。工具从 ToolContext 中解析任务和项目，不接受 Agent 自行指定其他项目；输出审计只保留记忆 ID、分类、摘要和分数，不保存正文。

## 9. 自动化验证

当前测试总数为 43，Memory 新增场景包括：

- 敏感信息脱敏和相同内容去重。
- 不同项目之间的检索隔离。
- 冲突事实进入候选态及人工验证。
- 代码修订变化导致旧记忆失效。
- Product Agent 引用已验证项目记忆。
- 任务阶段记忆产生并在完成后失效。
- 最新架构决策自动沉淀为 Project Memory。
- 重复沉淀保持幂等。

## 10. 当前边界与下一步

当前 Memory 检索为单进程、数据库全量候选集上的本地计算，适合 V1 和作品集演示。下一阶段建议进入异步执行与可观测性建设：引入后台 Worker、事件流与 SSE，使长任务脱离 HTTP 请求生命周期；随后开发 React 工作台，展示状态机、Agent 时间线、工具调用、RAG 引用和 Memory 引用链。

