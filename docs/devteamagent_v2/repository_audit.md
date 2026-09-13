# DevTeamAgent V2 Repository Audit

## 当前架构

DevTeamAgent 是 React/TypeScript 前端与 FastAPI 后端组成的前后端分离应用。后端以
`ExecutionManager` 执行持久化任务，以 `WorkflowService` 和确定性状态机编排需求、设计、
架构、开发、审查、测试和视觉验证。Agent 通过 Pydantic Artifact 交换信息；模型推理与
文件、检索、测试、Git 等受控动作分离。SQLAlchemy Repository 持久化 Task、Artifact、
Checkpoint、Event、Execution、ToolCall、Memory 与代码索引。

## 核心模块与已有能力

| 能力 | 当前真实实现 | V2 处理方式 |
| --- | --- | --- |
| Workflow | `backend/app/orchestrator/service.py`、`domain/state_machine.py` | 保留，继续负责宏观阶段、HITL、质量门禁 |
| Agent | `backend/app/agents/` 下 7 个 LLM Agent 和确定性 VisualReviewer | 接入统一 Harness，不重写角色业务逻辑 |
| LLM / Routing | `infrastructure/llm/router.py` | 复用路由和升档，补充运行时 Budget 约束 |
| Tool Calling | `tools/registry.py` 与 14 个已注册工具 | Registry 继续是唯一动作入口，接入 Skill/预算/停止策略 |
| 文件安全 | `path_policy.py`、`file_tools.py` | 复用路径约束、哈希并发控制、原子写和回滚 |
| Shell | `terminal.py` 的枚举 Runner、本地受限环境和 Docker executor | 保留白名单，不开放任意命令 |
| RAG | Scanner + Chunker + HashEmbedding + BM25/RRF | 复用，不另建索引 |
| Memory | `memory/service.py` | 复用检索、脱敏、冲突和任务完成后沉淀 |
| MCP | `mcp/server.py` + Gateway + 安全中间件 | 已是真实 stdio MCP；保持 3 个只读 Tool |
| Skill | Codex Host 可加载的 `skills/safe-code-change/SKILL.md` | 增加机器可校验配置和内部 SkillRegistry |
| Checkpoint | `checkpoints` 表和 `WorkflowService._checkpoint()` | 已扩展 Run、Artifact、Workspace、Budget、Repair、Context 引用 |
| Trace | Event、ToolCall、模型路由事件、SSE | 已复用 Event 作为 Harness Trace 存储 |
| Eval | 任务评分、策略基准、RAG 指标和实验汇总 | 已增加确定性 Tool Calling 轨迹评测 |

## 当前 Agent 调用链

`ExecutionManager -> WorkflowService -> Agent.run() -> RoutedStructuredModel / ToolRegistry`

模型调用会记录路由、Token 和延迟；工具调用会校验权限与 Pydantic 输入并持久化审计。
V2 已把各角色阶段调用纳入统一 Harness；各 Agent 仍保留领域专用的结构化 Prompt 和有限步骤，
Harness 在外层统一治理 Context、预算、Tool、停止、验证与 Trace。

## 可以直接复用的安全边界

- `ToolContext.permissions` 与 `ToolRegistry.invoke()`：权限、Schema、输入/输出审计。
- `WorkspacePathPolicy`：拒绝绝对路径、`..`、受保护目录及符号链接逃逸。
- `FileReplaceTool`：`expected_sha256`、唯一 `old_text` 与原子替换。
- `DeveloperAgent._rollback_mutations()`：批次失败后的反向恢复。
- `TerminalTool`：只接受 `TestRunner` 枚举，不能执行任意 Shell 字符串。
- MCP Gateway：服务端固定身份、权限与 workspace allowlist，所有结果经过 ToolRegistry。

## 主要问题

1. 现有 Workflow 只有 ExecutionScope 和 BUG_FIX 诊断分支，不是通用可配置 DAG。
2. 本地 executor 是受限进程但不是强隔离；Docker executor 才具有网络、能力和资源限制。
3. 尚未实现每任务 Git Worktree、workspace 记录和 Child Run Replay。
4. 尚未实现并行 SubTask、依赖图、expected_files 冲突调度和 Integrator。
5. 统一 ContextBuilder 以顶层字段为裁剪粒度；Developer 内部仍承担更细的代码 Chunk 和历史反馈压缩。
6. Verifier 已提供确定性证据模型，但 Developer 的最终 diff/test Postcondition 由后续 Workflow Gate 完成，
   不是在单次 Developer 阶段内一次性完成。
7. 真实模型消融 Benchmark 尚未运行，不能给出 V2 相对基线的提升数据。

## V2 改造映射

```text
WorkflowService（保留）
        │
        ▼
AgentHarness（新增统一运行时）
 ├─ ContextBuilder
 ├─ BudgetManager
 ├─ StopPolicy
 ├─ SkillRegistry
 ├─ Verifier
 └─ TraceManager -> 现有 Event 表/SSE
        │
        ├─ RoutedStructuredModel（复用并接入预算）
        └─ ToolRegistry（复用并接入 Skill/预算/Loop 检查）
               └─ 现有 File/RAG/Memory/Terminal/Git Tools
```

本次已完成 P0，以及 P1 中的 Context、Trace、Checkpoint 快照和轨迹 Eval。每任务 Worktree、
通用动态 DAG 与并行子任务仍应作为后续独立迁移处理，不能通过未接线的类或散落条件分支假装完成。
