# DevTeam Agent v0.8 实现说明

## 1. 本版本目标

v0.8 将 Agent 工作流从“HTTP 请求内同步执行”升级为“持久化作业 + 后台 Worker + 实时事件流”。原有同步接口继续保留，便于测试、兼容和单步调试；新增异步接口用于真实的长任务交互。

这一阶段主要解决三个工程问题：

- Agent、RAG、工具和测试可能运行较长时间，不应占用客户端 HTTP 请求生命周期。
- 客户端需要知道当前由哪个 Agent 执行、调用了什么工具、是否失败以及最终状态。
- 服务异常退出后，需要识别未完成作业并提供可追踪的恢复行为。

## 2. Execution Job 模型

每次异步工作流动作都会生成一条 Execution Record，包含：

- `id`、`task_id`：执行记录和所属任务。
- `action`：预定义工作流动作。
- `payload`：审批决定和反馈等结构化参数。
- `status`：`QUEUED`、`RUNNING`、`SUCCEEDED`、`FAILED` 或 `CANCELLED`。
- `attempt`：Worker 实际领取次数。
- `result_state`：执行结束后的任务状态。
- `error_message`：失败类型和经过截断的错误信息。
- 创建、开始和完成时间。

系统只接受以下五种动作，不接受任意函数名或命令字符串：

- `START`
- `DECIDE_PRD`
- `DECIDE_ARCHITECTURE`
- `RUN_REVIEW`
- `RUN_TESTS`

审批动作必须携带 `decision`，其他动作禁止携带审批参数，由 Pydantic 在进入队列前完成结构校验。

## 3. 并发治理

`executions.active_task_id` 是一个可空唯一列。作业处于 `QUEUED` 或 `RUNNING` 时，该列保存任务 ID；作业终止后清空。

该设计在 SQLite 和 PostgreSQL 中都能实现“同一任务最多一个活动作业”，并通过数据库唯一约束处理多个 API 请求并发到达的竞争条件。它可以防止两个审批动作、两次 Developer 执行或两轮测试同时修改同一任务。

不同任务可以并行执行，并发数由 `DEVTEAM_WORKER_CONCURRENCY` 控制，默认值为 1。

## 4. Worker 生命周期

FastAPI 使用 Lifespan 管理 ExecutionManager：

1. 应用启动时扫描数据库中的 `RUNNING` 作业。
2. 将它们恢复为 `QUEUED`，记录 `execution.recovered` 事件。
3. 将所有排队作业写入进程内 `asyncio.Queue`。
4. 启动指定数量的 Worker 协程。
5. Worker 使用条件更新原子领取作业，将状态改为 `RUNNING` 并增加 Attempt。
6. 根据 Action 调用已有 WorkflowService，不复制 Agent 编排逻辑。
7. 成功或失败后清理活动任务槽位，保存结果状态和错误信息。
8. 应用正常关闭时，Worker 会先处理已经进入队列的作业再退出。

Worker 捕获工作流异常并写入 Execution，而不是让后台协程静默退出。无效状态动作也会产生可查询的失败记录，之后同一任务仍可提交正确动作。

## 5. 持久化事件时间线

Execution 生命周期复用已有 Event 表，新增事件类型：

- `execution.queued`
- `execution.started`
- `execution.finished`
- `execution.recovered`
- `execution.requeued`

原有任务状态变化、Artifact 创建和 Tool 调用也写入同一事件表，因此事件 ID 构成单任务内可排序、可重放的统一时间线。

Repository 新增 `list_events_after(task_id, after_id)`，客户端无需重复下载全部历史。

## 6. SSE 实时事件流

`GET /api/v1/tasks/{task_id}/event-stream` 返回 `text/event-stream`：

- SSE 的 `id` 使用持久化 Event ID。
- `event` 使用领域事件类型。
- `data` 是完整 EventRecord JSON。
- `after_event_id` 用于刷新页面或断线后的增量恢复。
- `follow=false` 返回当前快照后关闭，适合测试和一次性同步。
- 长连接空闲时定期发送注释心跳。
- 响应设置 `Cache-Control: no-cache` 和 `X-Accel-Buffering: no`，避免常见反向代理缓冲。

当前实现采用短周期数据库轮询，优点是事件已经持久化、断线不丢失且无需额外中间件。进入多实例部署后，可以将通知通道替换为 PostgreSQL LISTEN/NOTIFY、Redis Streams 或消息队列，Event 表继续作为审计事实来源。

## 7. 可观测聚合接口

`GET /api/v1/tasks/{task_id}/observability` 一次返回：

- 当前 Task 状态。
- 全部 Execution 记录。
- 结构化 Artifact。
- ToolCall 审计记录。
- 事件总数。

该接口面向下一阶段 React 工作台，减少首屏需要发起的请求数量；实时增量则由 SSE 提供。

## 8. 新增 API

- `POST /api/v1/tasks/{task_id}/executions`
- `GET /api/v1/tasks/{task_id}/executions`
- `GET /api/v1/executions/{execution_id}`
- `POST /api/v1/executions/{execution_id}/cancel`
- `GET /api/v1/tasks/{task_id}/event-stream`
- `GET /api/v1/tasks/{task_id}/observability`

取消接口当前只允许取消 `QUEUED` 作业。运行中的 Agent 或外部测试进程需要协作式取消和工具级终止信号，这部分没有用不安全的强制状态覆盖来伪装完成。

## 9. 自动化验证

当前自动化测试总数为 47。v0.8 新增覆盖：

- 五个异步动作完成整个研发工作流。
- Execution 状态、Attempt 和 Result State 正确持久化。
- 错误动作进入 `FAILED`，记录原因并释放任务活动槽位。
- 同一任务活动作业唯一约束。
- 排队作业取消。
- Worker 中断记录恢复为排队状态。
- 任务可观测聚合结果。
- SSE 快照格式和按 Event ID 断点续传。

## 10. 当前边界与下一步

当前 Worker 和队列位于单个 API 进程，数据库保证作业状态和互斥，但尚未提供分布式租约、心跳和多实例抢占。重启恢复是“至少一次”语义；Artifact、Memory 沉淀等关键写入已有版本或指纹幂等保护，但外部工具的通用幂等键仍需继续建设。

下一阶段建议开发 React 研发工作台，重点展示任务状态机、Agent 执行时间线、审批面板、ToolCall、RAG 引用和 Memory 引用，并直接消费本版本提供的 Observability 与 SSE 接口。

