# Agent Harness

实现位于 `backend/app/agent_runtime/`，由 `ApplicationContainer` 构造并注入
`WorkflowService`。Product、Diagnostic、Designer、Architect、Developer、Reviewer、Tester
和确定性 Visual Reviewer 的阶段调用都经过 `AgentHarness.run_phase()`。

一次 Run 的状态为 `RUNNING / WAITING_TOOL / VERIFYING / REPAIRING / WAITING_HUMAN /
COMPLETED / FAILED / CANCELLED`。当前 Agent 的模型调用由 `RoutedStructuredModel` 计入 LLM、
Step 和 Token 预算；工具调用由 `ToolRegistry` 计入 Tool、Step、Changed Files 预算。

确定性停止条件包括超时、LLM/Tool/Step/Token/修改文件/修复轮次超限、相同工具及参数连续
三次、相同错误连续三次和多轮上下文引用不变。STRICT 任务在有价值中间状态触发 Harness
限制时写入 `HITL_REQUIRED` 并暂停到最近工作流状态；其他任务失败并沿用现有 Checkpoint
恢复入口。

Harness 不暴露模型隐藏思维。Trace 只记录 run_id、Agent、状态、模型档位、Token 数、工具名、
参数字段、耗时和错误摘要。
