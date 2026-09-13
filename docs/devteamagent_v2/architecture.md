# DevTeamAgent V2 架构

```text
User / React
      │ REST + SSE
      ▼
FastAPI ── ExecutionManager ── SQLAlchemy/SQLite|PostgreSQL
                    │
                    ▼
          WorkflowService + State Machine
             │ HITL / Gate / Checkpoint
             ▼
              AgentHarness
       ┌────────┼─────────┐
 ContextBuilder Budget  StopPolicy
       │        │          │
       └──── SkillRegistry ┘
                │
      RoutedModel + ToolRegistry
                │
    RAG / Memory / File / Test / Git
```

`WorkflowService` 管宏观业务阶段、Artifact 生命周期、人工审批和质量门禁；
`AgentHarness` 管单个 Agent Run 的状态、上下文预算、模型/工具预算、循环停止、验证和 Trace。
Agent 负责理解与结构化推理；Skill 是机器可加载的 SOP 和权限收窄；MCP 是外部客户端的
只读上下文协议；ToolRegistry 是所有内部工具动作的唯一执行与审计入口。

RAG、Memory 和 Artifact 都是 Context 来源，但生命周期不同：RAG 来自代码索引，Memory
来自经治理的历史经验，Artifact 是当前任务的结构化阶段交付物。Checkpoint 保存可恢复边界，
Event/ToolCall 保存可观测证据，Evaluation 对结果和轨迹做确定性计算。
