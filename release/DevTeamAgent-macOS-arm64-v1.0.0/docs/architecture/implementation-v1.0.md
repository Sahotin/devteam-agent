# DevTeam Agent v1.0 实现说明

## 1. 版本目标

v1.0 将此前完成的 Multi-Agent、工具、RAG、Memory、异步执行和 React 工作台整合为可配置、可迁移、可观测、可容器化交付的完整工程基线。

本版本重点补齐：

- 真实 LLM Provider，而不是只能运行本地 Demo Model。
- 密钥和数据库凭证的安全配置边界。
- 可重复、可回滚的数据库迁移。
- 面向生产排障的日志、请求关联、健康检查与指标。
- 后端、前端和 PostgreSQL 一键容器化启动。
- 自动化和真实 HTTP 两级验收。

## 2. OpenAI Structured Model Provider

### 2.1 Provider 选择

`DEVTEAM_LLM_PROVIDER` 支持：

- `demo`：确定性本地 Provider，不发起网络请求，适合测试、开发和面试演示。
- `openai`：使用官方 Python SDK 和 Responses API。

当 Provider 为 `openai` 时，配置层强制要求 `OPENAI_API_KEY`。缺少 Key 会在应用构建前失败，不允许运行到 Agent 阶段后才产生模糊错误。

### 2.2 结构化输出

OpenAI Provider 使用：

```text
client.responses.parse(
  model=...,
  input=[system, user],
  text_format=PydanticArtifactSchema,
  store=False
)
```

官方文档说明 Responses API 可以将 Pydantic Model 作为 `text_format` 并通过 `output_parsed` 返回结构化对象；Structured Outputs 相比普通 JSON Mode 还能保证 Schema 一致性。参考 [OpenAI Structured Outputs 官方文档](https://developers.openai.com/api/docs/guides/structured-outputs)。

Product、Architect、Developer、Reviewer 和 Tester 继续复用同一个 `StructuredModel` Protocol，因此模型 Provider 的切换不改变 Agent、Orchestrator 或 Artifact 领域层。

### 2.3 隐私与错误边界

- 请求设置 `store=False`。
- 用户 Payload 使用 UTF-8 JSON 传入，不拼接到系统 Prompt。
- API Key 和 Base URL 不参与 Dataclass `repr`。
- Capability API 只返回 Provider 和 Model，不返回 Key、Base URL 或数据库地址。
- 没有结构化 `output_parsed` 时抛出明确的 Provider 错误，不把空结果伪装为有效 Artifact。
- SDK Timeout 和 Max Retries 可通过环境变量配置。

本地自动化使用 Fake Client 验证请求形状和解析行为，没有读取或使用真实 API Key，也没有产生外部 API 费用。

## 3. 配置治理

Settings 对以下配置进行启动时校验：

- LLM Provider 必须为 `demo` 或 `openai`。
- OpenAI Provider 必须有 API Key。
- Worker 并发数必须大于等于 1。
- OpenAI Timeout 必须为正数。
- SDK 重试次数不能为负数。
- Boolean 环境变量只接受明确的 true/false 表达。

数据库 URL、API Key 和可选 Base URL 均从环境变量进入内存，不提供写入 API，也不会序列化到领域 Artifact。

## 4. Alembic 数据库迁移

v1.0 引入 Alembic，初始迁移覆盖：

- Project、Task、Artifact、Checkpoint、Event。
- ToolCall、Execution。
- IndexedFile、CodeChunk。
- Memory。

迁移保留唯一约束、外键级联和查询索引。自动化测试在独立 SQLite 数据库执行 `upgrade head` 和 `downgrade base`，验证迁移可建立和完整回滚 Schema。

开发测试默认 `DEVTEAM_DATABASE_AUTO_CREATE=true`，保持轻量体验。生产 Compose 设置为 `false`，只允许 Alembic 管理 Schema。

对于 v1.0 之前由 `create_all` 创建的已有数据库，应先备份并确认 Schema 与初始迁移一致，再执行 `alembic stamp head`；不能直接在未知 Schema 上执行初始 Upgrade。

## 5. 结构化日志与请求关联

HTTP Middleware 为每个请求生成或校验 `X-Request-ID`：

- 只接受长度不超过 128 的字母、数字、点、下划线和连字符。
- 非法输入会被替换为服务端 UUID。
- 响应始终返回最终 Request ID。
- Request ID 通过 ContextVar 注入同一异步上下文的结构化日志。

生产 JSON 日志包含时间、级别、Logger、消息、Request ID、Method、Route、Status 和 Duration，不记录请求正文、Header、API Key 或 Artifact 内容。

`DEVTEAM_LOG_JSON=true` 启用 JSON 格式；本地默认保留便于阅读的文本日志。

## 6. 健康检查与指标

系统提供：

- `/api/v1/health`：进程存活检查。
- `/api/v1/ready`：同时检查数据库连接和 Execution Worker。
- `/api/v1/metrics`：Prometheus 文本格式指标。

指标包括按 Method、Route 和 Status 聚合的请求数、累计响应时间和当前活动请求数。Route 使用 FastAPI 路由模板，避免把 Task ID 等动态路径写入 Label 造成高基数。

## 7. Docker 与 Compose

Dockerfile 使用两个阶段：

1. Node 阶段使用冻结 Lockfile 安装依赖并构建 React。
2. Python 阶段安装后端、复制前端静态产物并以非 root 用户运行。

运行镜像具备以下约束：

- 非 root `devteam` 用户。
- 禁止 Python 写入 `.pyc`。
- Readiness Healthcheck。
- 启动前执行 `alembic upgrade head`。

Compose 提供 App 和 PostgreSQL：

- 数据库 Healthcheck 成功后才启动 App。
- App 根文件系统只读。
- `/tmp` 使用 Tmpfs。
- 删除全部 Linux Capabilities。
- 启用 `no-new-privileges`。
- 只把 `demo-workspace` 挂载为 Agent 工作区。
- API Key 通过环境变量注入，不写入镜像。

当前 Compose 的本地默认密码只用于开发体验，共享环境必须覆盖。

## 8. 冒烟测试

`scripts/smoke_test.py` 面向已经运行的服务执行：

1. Readiness 检查。
2. 创建项目与任务。
3. 异步启动 Product Agent。
4. 批准 PRD。
5. 批准 Architecture。
6. 执行 Reviewer。
7. 执行 Tester。
8. 验证最终状态和 Observability。

本次本地验收结果：

```text
status: passed
final state: COMPLETED
artifacts: 5
tool calls: 8
events: 46
```

冒烟测试使用 Demo Provider，没有外部模型费用。产生的临时数据库和 Workspace 在验证后已删除。

## 9. 自动化验证

v1.0 验证范围包括：

- OpenAI Responses API 调用形状、`store=False` 和 Pydantic 解析。
- Provider 缺失 Key 的启动失败。
- Settings `repr` 不暴露 API Key 和数据库密码。
- Request ID 透传与非法 ID 替换。
- JSON 日志字段和请求关联。
- Readiness 和 Prometheus 指标。
- Alembic Upgrade/Downgrade。
- Compose 密钥占位、只读文件系统和能力裁剪。
- Dockerfile 非 root 用户、迁移优先和 Healthcheck。
- 原有 Multi-Agent、Tool、RAG、Memory、Worker、SSE 和前端回归测试。

## 10. 已知边界

- 当前环境没有配置真实 OpenAI API Key，因此完成的是官方 SDK 适配和 Mock 验证，没有发起真实模型请求。
- 当前机器没有 Docker CLI，因此无法在本机执行真实 `docker compose build/up`；部署清单已由自动化测试解析，仍建议在安装 Docker 的机器上执行一次容器验收。
- Worker 仍为单 API 进程内队列；多实例分布式执行需要租约、心跳和消息中间件。
- Hash Embedding 仍是离线基线，生产语义检索可替换为真实 Embedding Provider 和向量数据库。
- 身份认证和团队 RBAC 尚未纳入 v1.0。

这些边界均在 Capability、README 或本文档中明确说明，没有把未执行的外部集成声明为已验证。

