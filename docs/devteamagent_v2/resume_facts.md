# Resume Facts

## Implemented

- 自定义状态机编排结构化 Multi-Agent 软件研发流程，支持 HITL、质量门禁、Checkpoint 和恢复。
- 统一 Agent Harness 对 LLM/Tool/Token/Step/文件/超时预算、循环停止和运行 Trace 进行约束。
- `safe-code-change` 同时具有 Host 指令与机器配置，服务端强制工具白名单和写前检索/读取。
- 真实 stdio MCP 通过 ToolRegistry 暴露三个 task-scoped 只读上下文工具。
- 代码检索复用 Hash Embedding、BM25 与 RRF；Memory 具有状态、范围、脱敏、冲突和沉淀规则。
- 文件工具具备路径沙箱、哈希并发控制、原子写和失败回滚；测试命令采用枚举白名单。
- Event、ToolCall、模型用量、SSE 与确定性结果/轨迹评测形成可审计证据链。

## Measured

- 不在此文件固化会随提交变化的通过数、覆盖率或性能数字；以 CI 与最新测试报告为准。
- 未执行真实模型对照 Benchmark，因此没有质量提升或 Token 节省结论。

## Not Implemented

- 每任务 Git Worktree 自动隔离。
- 通用动态 Workflow DAG、并行 Sub-Agent 与冲突 Integrator。
- HTTP/远程 MCP、OAuth、MCP 写文件/Shell/Git。
- 外部向量数据库和真实语义 Embedding。

## Optional Future Work

- 持久化 Workspace/AgentRun/Step，支持 Child Run Replay。
- 用固定 Fixture 跑真实模型消融实验并报告置信区间。
- 将实时 Context 长度和剩余成本预算纳入首档模型路由。
