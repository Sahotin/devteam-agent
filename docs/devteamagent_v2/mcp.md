# MCP

真实 stdio Server 位于 `backend/app/mcp/server.py`，入口为 `devteam-mcp`。第一版仅暴露
`get_project_context`、`search_code` 和 `query_memory`。三者均由 `McpToolGateway` 构造固定
身份和固定权限，再调用 `ToolRegistry.invoke()`；MCP 不直接绕过 Registry 调 Repository、RAG
或 Memory 返回业务结果。

启动前必须配置 `DEVTEAM_MCP_ALLOWED_WORKSPACE_ROOTS`。客户端只能传 task_id 与查询参数，
不能传 workspace、permissions、agent_name 或内部 tool_name。代码索引可能写索引表，因此
`search_code` 在任务存在 QUEUED/RUNNING Execution 时返回结构化冲突错误。

MCP 不提供文件写入、Shell、Git、Workflow 控制和 Memory 写入。测试
`backend/tests/test_mcp_server.py` 使用真实 MCP Client 与 stdio 子进程验证工具发现、调用、
Schema、allowlist、活动执行冲突和隐藏参数负例。
