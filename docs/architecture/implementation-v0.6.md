# DevTeam Agent v0.6 实现基线（代码 RAG）

## 当前范围

当前可执行工作流覆盖以下流程：

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

第一大阶段的五类 Agent、任务控制、Git 工具和可选 Docker 执行器保持完整。当前版本进入第二大阶段，新增代码仓库增量索引、语言感知切片、离线向量、BM25、Hybrid Retrieval 和 Developer RAG 上下文。

## 架构边界

- `domain`：定义任务状态、状态转换规则和强类型 Artifact。
- `agents`：实现不同角色的 Prompt 和结构化模型调用。
- `orchestrator`：负责工作流决策、审批路由和 Checkpoint 管理。
- `infrastructure`：提供 SQLAlchemy 持久化和模型供应商适配器。
- `tools`：实现工具注册、权限校验、工作区路径保护和调用审计。
- `api`：只负责 HTTP 传输和参数校验，不包含工作流业务规则。

## 持久化保证

- Task 状态转换使用单调递增的 `state_version`。
- 状态更新必须携带预期版本号，用于检测并发执行和重复推进。
- 每次状态转换和 Artifact 创建都会生成审计事件。
- PRD 与 Architecture Artifact 创建后不可修改，通过版本号管理修订内容。
- 任务进入稳定的审批状态后会创建 Checkpoint。
- Agent 执行失败时，任务会进入 `FAILED` 状态，并保存长度受限的诊断信息。
- 每次已注册、未注册、参数非法、权限不足、执行成功或执行失败的工具调用都会保存 ToolCall 状态和审计事件。

## Developer Agent 与工具治理

Developer Agent 首先通过 Code Search Tool 检索已有代码，再读取有限数量的候选文件，并生成经过 Pydantic 校验的 `DeveloperPlan`。计划中的文件变更不会由模型直接执行，而是交给 Tool Registry。

当前工具层提供：

- `file.read`：读取工作区内的 UTF-8 文本文件，审计日志不保存正文。
- `file.create`：使用排他创建语义创建新文件，拒绝覆盖已有文件。
- `file.replace`：校验文件 SHA-256，并要求旧文本只出现一次。
- `code.search`：在工作区内进行文本或正则搜索，跳过依赖目录和敏感文件。

所有工具路径必须相对于项目工作区。系统拒绝路径逃逸、`.git`、`.venv`、`.env.*`、私钥和证书私钥文件。文件内容、替换文本和搜索片段不会写入 ToolCall 审计记录。

## Reviewer Agent 与质量门禁

Reviewer Agent 与 Developer Agent 职责隔离，只拥有 `file:read` 和 `code:search` 权限。审查开始前，Reviewer 会重新读取 `CodeChangeArtifact` 中的全部变更文件，并验证文件当前 SHA-256 是否仍与 Artifact 一致，避免审查过期内容。

`ReviewArtifact` 包含：

- 审查结论：`APPROVED` 或 `CHANGES_REQUESTED`。
- 完整审查文件列表。
- 问题严重级别：`BLOCKER`、`MAJOR`、`MINOR`、`NIT`。
- 问题证据、影响、位置、修复建议和需求编号。
- 安全检查说明。

质量门禁规则：

- `APPROVED` 不能包含 `BLOCKER` 或 `MAJOR`。
- `CHANGES_REQUESTED` 至少包含一个 `BLOCKER` 或 `MAJOR`。
- Review 引用的文件必须属于当前代码变更。
- Review 引用的需求编号必须存在于当前 PRD。
- 审查批准后，任务进入 `TESTING`。
- 审查阻断后，完整 Review Artifact 返回 Developer，生成下一版 Code Change。
- 同一任务最多允许三版代码变更，超过限制后任务进入 `FAILED`。

## Terminal Tool

Terminal Tool 不接受任意 Shell 字符串，也不提供自由命令参数。Tester 只能从以下预定义 Runner 中选择：

- `PYTHON_COMPILE`：通过当前 Python 解释器执行 `compileall`。
- `PYTEST`：通过当前 Python 解释器执行 pytest。
- `UNITTEST`：执行 Python unittest discovery。
- `NPM_TEST`：执行项目声明的 npm test。
- `MAVEN_TEST`：执行 Maven test。

执行器具备以下约束：

- 工作目录固定为项目 Workspace。
- 使用 `shell=False`，不经过 Shell 解析命令字符串。
- Tool Input 禁止任何未声明字段，因此不能附加自定义命令或参数。
- 超时时终止当前进程及其子进程树。
- 标准输出和错误输出分别限制为 64KB，超出部分继续消费但不保存，防止子进程阻塞。
- ToolCall 审计只保存 Runner、退出码、耗时和截断状态，不保存命令输出正文。
- 传递给子进程的环境变量采用白名单。

当前执行器标记为 `local-restricted`。它可以限制命令入口和资源使用方式，但不能提供容器级网络及文件系统隔离，因此不应执行不可信仓库。Docker Sandbox 完成后才能将其标记为沙箱执行器。

## Tester Agent 与测试闭环

Tester Agent 根据 PRD、架构设计、最新 Code Change 和已批准 Review 生成结构化 `TestPlan`。每个测试命令必须映射至少一个验收标准，并且所有验收标准都必须被测试计划覆盖。

`TestReportArtifact` 保存：

- `PASSED`、`FAILED` 或 `ENVIRONMENT_ERROR` 结论。
- Runner、退出码、耗时、截断状态和有限日志摘要。
- 验收标准到测试命令的映射。
- Python、平台和执行器信息。
- 当前测试能力限制。

日志进入 Test Report 前会脱敏常见的 Password、Token、Secret、API Key 和 Bearer 凭证模式。

工作流规则：

- 全部命令成功：`TESTING → FINAL_VALIDATION → COMPLETED`。
- 测试断言失败：Test Report 返回 Developer，生成新代码版本并重新经过 Reviewer。
- 连续两次测试失败：任务进入 `FAILED`，避免无限返工。
- 超时或缺少运行环境：记录 `ENVIRONMENT_ERROR` 并终止任务，不错误修改代码。

## 任务暂停、恢复与取消

当前 API 执行仍是同步请求模型，因此暂停功能只允许在稳定边界使用：

- `CREATED`
- `PRD_APPROVAL`
- `ARCH_APPROVAL`
- `REVIEWING`
- `TESTING`

暂停时任务转换为 `PAUSED`，并写入包含 `resume_state`、原因和状态版本的持久化 Checkpoint。恢复时读取最新暂停 Checkpoint，通过乐观锁恢复原状态。运行中的模型调用和子进程不会被伪装成可中断任务；真正的运行时中断将在后台 Worker 阶段实现。

任务可以在稳定非终态取消为 `CANCELLED`。`COMPLETED`、`FAILED` 和 `CANCELLED` 都是终态，不能再次取消或恢复。

## Git Tool 与提交质量门禁

工具层提供：

- `git.status`：读取分支和工作区变更。
- `git.diff`：读取限定文件的差异，并对审计日志隐藏正文。
- `git.log`：读取最多 50 条提交历史。
- `git.commit`：提交经过最终质量门禁的任务文件。

Git 命令始终使用参数数组和 `shell=False`，工作目录必须等于 Git 仓库根目录，不能借用父目录仓库。输出设置 128KB 上限，执行设置 30 秒超时。

`git.commit` 不能由 Developer Agent 直接调用，只能由 Orchestrator 的最终质量门禁临时授权。提交前必须满足：

1. Task 状态为 `COMPLETED`。
2. 最新 Review 为 `APPROVED`。
3. 最新 Test Report 为 `PASSED`。
4. 提交文件完全来自最新 CodeChange Artifact。
5. 每个文件当前 SHA-256 与 CodeChange Artifact 一致。
6. 同一个 Task 尚未生成 Git Commit Artifact。

提交使用文件范围参数，保留与当前任务无关的已暂存文件。提交成功后保存包含 Commit SHA、消息、文件列表和 CodeChange 版本的 Git Commit Artifact。

## Docker Terminal Executor

通过 `DEVTEAM_TERMINAL_EXECUTOR=docker` 可以选择 Docker 执行器。容器命令固定包含：

- `--network none`
- CPU、内存和 PID 限制
- `--cap-drop ALL`
- `no-new-privileges`
- 只读容器根文件系统
- 受限 `/tmp` tmpfs
- 只挂载当前项目 Workspace

不同 Runner 使用固定镜像和固定命令，不允许模型提供镜像、挂载、网络或额外参数。当前开发机器没有安装 Docker，因此已完成命令构造、缺失运行时诊断和模拟执行测试，但尚未完成真实 Docker daemon 集成验收。默认继续使用 `local-restricted` 执行器。

Python Runner 使用项目提供的 `devteam-agent/python-runner:0.5.0` 镜像定义，以非 root 用户运行并预装固定版本 pytest。Node 和 Maven 项目的第三方依赖仍需要预构建项目专用镜像或安全依赖缓存，这部分属于后续沙箱增强范围。

## 模型集成

默认模型供应商是确定性的本地 Demo Provider，因此无需配置 API Key 也可以演示完整工作流。Demo Provider 在 Developer 阶段只创建需求和架构追踪文档；Reviewer 会验证文件一致性；Tester 默认执行 Python 编译检查，并在报告中明确该检查不能替代业务单元测试或集成测试。生产环境的模型适配器必须实现 `StructuredModel` 接口，并返回经过 Pydantic 校验的结构化输出。任何特定模型供应商的 SDK 类型都不能泄漏到 Agent 或领域模型中。

## 代码仓库索引

Repository Scanner 只处理项目 Workspace 内受支持的 UTF-8 文本文件，并跳过：

- `.git`、`.venv`、`node_modules` 和缓存目录。
- `.env.*`、私钥和证书私钥文件。
- `.devteam`、构建输出、覆盖率和数据目录。
- 超过 1MB、二进制、非 UTF-8 和符号链接文件。

每个文件保存 SHA-256。再次索引时，内容哈希未变化的文件不会重新切片和计算向量；修改文件会原子替换其 Chunk；已删除文件会从索引中清除。

## 语言感知切片

- Python：使用标准库 AST 按顶层函数、异步函数和类切片，保留装饰器、模块前缀、符号间代码和模块尾部。
- Markdown：按标题层级形成 Section Chunk。
- TypeScript、JavaScript、Java、JSON、YAML、TOML 和 SQL：当前使用最多 120 行、重叠 15 行的窗口切片，并提取可识别的首个符号。
- 单个 Chunk 内容限制为 16,000 字符。

每个 Chunk 保存 Project、文件路径、语言、符号名称、符号类型、起止行号、内容哈希、正文和向量。

## Embedding 与 Hybrid Retrieval

当前 `HashEmbeddingProvider` 将代码 Token 稳定哈希到 256 维向量并进行 L2 归一化。它无需模型下载和 API Key，可以保证测试可重复，但只是一种词法近似向量，不应宣传为高质量语义 Embedding。

Hybrid Retrieval 由以下部分组成：

1. 代码感知 Tokenization，拆分 snake_case、camelCase 和中文字符。
2. BM25 关键词评分。
3. Hash Vector 余弦相似度。
4. Reciprocal Rank Fusion 合并两个排序。
5. 精确符号名与文件路径加权。
6. 语言和路径前缀过滤。

Developer Agent 在编码前调用 `rag.search`。该工具先执行增量索引，再返回 Top-K Chunk；随后使用 Code Search 补充精确匹配。注入模型的检索上下文总量限制为 32,000 字符，ToolCall 审计不保存代码正文。

提供以下 API：

- `POST /api/v1/projects/{project_id}/index`
- `GET /api/v1/projects/{project_id}/index/stats`
- `POST /api/v1/projects/{project_id}/search`

## 下一开发增量

1. 实现 Short-term、Project 和 Long-term Memory。
2. 增加 Tree-sitter 多语言符号解析。
3. 增加真实 Embedding Provider 和 Chroma Vector Store Adapter。
4. 引入后台 Worker、SSE 事件和 React Dashboard。
5. 引入 LangGraph 作为可持久化工作流图适配器，同时保留领域状态机作为合法状态转换的事实来源。
6. 在具备 Docker daemon 的环境中完成真实容器集成验收。
