# DevTeam Agent

DevTeam Agent 是一个面向软件研发场景、由结构化产物驱动的 Multi-Agent 协作系统。当前版本为 v1.0.0，已完成五 Agent 研发闭环、受治理工具系统、代码 Hybrid RAG、分层 Memory、后台执行、实时可观测、React 工作台和生产化交付基线。

## 已实现能力

- Product、Architect、Developer、Reviewer、Tester 五个 Agent 协作
- PRD 与架构人工审批、审查与测试失败自动返工
- 任务状态机、Checkpoint、暂停、恢复、取消和失败限制
- File、Code Search、Terminal、Git、RAG、Memory 工具及权限审计
- 安全路径校验、文件哈希并发保护、测试命令白名单和输出脱敏
- Git 提交前质量门禁和文件哈希一致性校验
- 仓库增量索引、语言感知切片、Hash Embedding、BM25 与 RRF 混合检索
- Short-term、Project、Long-term 三层 Memory
- Memory 脱敏、去重、冲突候选、人工验证、版本失效和任务完成后沉淀
- Agent 产物记录实际引用的代码上下文及记忆 ID，支持全链路审计
- 持久化 Execution Job、后台 Worker 和服务重启恢复
- 单任务活动作业互斥、失败审计、排队取消和可配置 Worker 并发度
- SSE 实时事件流、事件 ID 断点续传、心跳及任务可观测聚合接口
- React、TypeScript、Vite 构建的响应式研发工作台
- 项目与任务管理、PRD/架构审批、Review/Test 操作面板
- Agent 实时时间线、Artifact、ToolCall、Memory 引用审计视图
- Demo/OpenAI 双模型 Provider，基于 Responses API Structured Outputs
- API Key、数据库凭证和配置项的显式安全边界
- Alembic 数据库迁移及 SQLite/PostgreSQL 双环境
- JSON 结构化日志、Request ID、Readiness 和 Prometheus 指标
- 非 root 多阶段 Docker 镜像与 PostgreSQL Compose 一键启动
- 可重复执行的完整工作流冒烟验证脚本
- SQLite 持久化与 FastAPI 接口

## 工作流

```text
CREATED
  -> REQUIREMENT_ANALYZING
  -> PRD_APPROVAL
  -> ARCHITECTING
  -> ARCH_APPROVAL
  -> CODING
  -> REVIEWING
  -> TESTING
  -> FINAL_VALIDATION
  -> COMPLETED
```

Reviewer 返回 `CHANGES_REQUESTED` 或测试失败时，工作流会携带结构化反馈返回 Developer。系统限制最大返工次数，避免 Agent 无限循环。只有最新 Review 为 `APPROVED`、测试为 `PASSED` 且文件哈希与 CodeChange Artifact 一致时，编排器才会授予一次 Git Commit 权限。

## Memory 工作方式

- Short-term Memory 保存当前任务每一阶段的结构化产物，任务完成后自动标记为 `STALE`。
- Project Memory 保存已验证的架构决策和项目约定。
- Long-term Memory 保存已在完成任务中得到验证的审查修复和测试失败经验。
- 所有写入先进行敏感信息脱敏，并使用规范化内容指纹去重。
- 带相同 `conflict_key` 的矛盾事实不会直接覆盖旧事实，而是降级为 `CANDIDATE`，等待人工验证。
- 记忆检索使用 BM25、Hash Embedding 与 Reciprocal Rank Fusion，并结合可信度和状态加权。

## 后台执行与事件流

客户端可以通过 `POST /api/v1/tasks/{task_id}/executions` 提交 `START`、`DECIDE_PRD`、`DECIDE_ARCHITECTURE`、`RUN_REVIEW` 或 `RUN_TESTS` 动作，并立即获得持久化 Execution ID。Worker 在后台执行工作流，客户端通过 Execution 查询接口或 SSE 事件流跟踪进度。

每个任务最多存在一个 `QUEUED` 或 `RUNNING` 作业。服务重启时，未完成的 `RUNNING` 作业会恢复为 `QUEUED` 并重新进入队列。`DEVTEAM_WORKER_CONCURRENCY` 可以配置单进程 Worker 数量，默认值为 1。

## 本地启动

1. 准备 Python 3.11+ 环境。
2. 安装开发依赖：`pip install -e ".[dev]"`。
3. 启动服务：`uvicorn backend.app.main:app --reload`。
4. 打开 `http://127.0.0.1:8000/docs`。

前端开发模式：

1. 进入 `frontend`。
2. 执行 `pnpm install`。
3. 执行 `pnpm dev`。
4. 打开 `http://127.0.0.1:5173`。

前端开发服务器会将 `/api` 代理到 `http://127.0.0.1:8000`。执行 `pnpm build` 后，FastAPI 会自动托管 `frontend/dist`，此时可以直接打开 `http://127.0.0.1:8000` 使用完整工作台。

## 使用真实模型

默认使用确定性的 Demo Provider，适合测试和项目演示。切换 OpenAI Provider：

```text
DEVTEAM_LLM_PROVIDER=openai
DEVTEAM_LLM_MODEL=gpt-5.6-sol
OPENAI_API_KEY=your-api-key
```

系统使用 OpenAI Responses API Structured Outputs 将 Pydantic Artifact Schema 直接作为输出约束，并设置 `store=false`。API Key 只从环境变量读取，不会出现在 Capability API、Artifact、Memory、ToolCall 或日志中。模型可用性取决于 API 账户权限，也可通过 `DEVTEAM_LLM_MODEL` 显式覆盖。

## Docker Compose

```text
docker compose up --build
```

Compose 会启动 PostgreSQL，等待数据库健康后执行 `alembic upgrade head`，再启动 API、Worker 和前端工作台。默认地址为 `http://127.0.0.1:8000`。

若使用真实模型，请在未提交到版本库的 `.env` 中设置 `OPENAI_API_KEY`；生产或共享环境必须同时修改默认 PostgreSQL 密码。

默认使用 SQLite，数据库位于 `data/devteam_agent.db`。当前 Demo Model 用于稳定演示结构化工作流；它只会在目标项目的 `.devteam/tasks/` 下生成可追踪的实现计划，不会虚构业务代码或测试已经完成。

Terminal Tool 默认使用本地受限执行器，仅接受 `PYTHON_COMPILE`、`PYTEST`、`UNITTEST`、`NPM_TEST` 和 `MAVEN_TEST` 五种预定义 Runner。也可以通过 `DEVTEAM_TERMINAL_EXECUTOR=docker` 启用关闭网络、限制资源和移除 Linux capabilities 的 Docker 执行器。

## 验证

```text
python -m pytest backend/tests -q
python -m compileall -q backend
python -m pip check
cd frontend && pnpm test
cd frontend && pnpm build
python scripts/smoke_test.py
```

详细中文实现说明见 [docs/architecture/implementation-v1.0.md](docs/architecture/implementation-v1.0.md)，最终验收结论见 [docs/milestones/v1.0验收报告.md](docs/milestones/v1.0验收报告.md)。

## DeepSeek 配置

系统支持通过独立适配器接入 DeepSeek Chat Completions API。适配器会请求 JSON Output，并在本地执行 JSON 解析、Pydantic Schema 校验和有限重试。

```env
DEVTEAM_LLM_PROVIDER=deepseek
DEVTEAM_LLM_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=your-new-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEVTEAM_DEEPSEEK_TIMEOUT_SECONDS=120
DEVTEAM_DEEPSEEK_MAX_RETRIES=1
```

真实密钥只能保存在已被 Git 忽略的 `.env` 或部署平台的 Secret 中，禁止写入源码、文档、Artifact、Memory 或日志。
本地服务启动时会自动读取项目根目录的 `.env`，但不会覆盖操作系统中已经设置的环境变量。
默认使用低延迟的 `deepseek-v4-flash`；需要更强的复杂推理质量时，可改回 `deepseek-v4-pro`。

## Windows 一键启动

在项目根目录双击 `启动 DevTeam Agent.bat`。启动器会检查虚拟环境与 `.env` 配置，在服务健康检查通过后自动打开浏览器。运行窗口会持续显示后端日志；需要停止服务时，在该窗口按 `Ctrl+C`。

## macOS Apple Silicon 一键部署

适用于 M1/M2/M3/M4 Mac。首次解压后执行 `bash scripts/install_macos.sh`，安装完成后双击 `启动 DevTeam Agent.command`，或执行 `bash scripts/start_macos.sh`。详细步骤见 [macOS部署说明.md](macOS部署说明.md)。部署包不得包含 `.env`、历史数据库、Windows `.venv` 或 `node_modules`。
