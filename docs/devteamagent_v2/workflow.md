# Workflow

状态机位于 `backend/app/domain/state_machine.py`，执行位于
`backend/app/orchestrator/service.py`。Workflow 管理 PRD/架构 HITL、Artifact、Review/Test/Visual
Gate、失败返工、暂停、取消和 Checkpoint。

流程并非完全固定：`PLAN_ONLY` 在架构后停止，`WORK_ONLY` 在代码后停止，`REVIEW_ONLY` 在
审查后停止，完整任务进入测试和最终验证；BUG_FIX 会增加 Diagnostic 与真实运行/交互证据。
这些分支由确定性 TaskPolicy 和 ExecutionScope 控制。

当前尚未实现通用 TaskClassifier + 可配置 DAG，也没有并行 SubTask 调度或 Integrator。
因此现阶段应称为“带确定性分支的状态机工作流”，不应声称已经实现任意动态 Agent 图。
