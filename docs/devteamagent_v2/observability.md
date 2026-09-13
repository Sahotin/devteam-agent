# Trace 与可观测性

Harness 复用现有 Event 表和 SSE，不新增重复 Trace 存储。新增事件包括 `AGENT_STARTED`、
`CONTEXT_BUILT`、`LLM_REQUEST`、`LLM_RESPONSE`、`TOOL_REQUEST`、`TOOL_RESULT`、
`VERIFY_STARTED`、`VERIFY_RESULT`、`AGENT_COMPLETED`、`RUN_FAILED`、`HITL_REQUIRED` 和
`CHECKPOINT_CREATED`。模型路由的既有事件和 ToolCall 表继续保留。

事件通过 `/api/v1/tasks/{task_id}/events/stream` 实时发送，支持事件 ID 续传和 heartbeat；
工作台现有 Timeline 和 Inspector 可显示这些结构化事件。Prompt、业务 payload 正文、文件正文
和 API Key 不进入 Harness Trace。
