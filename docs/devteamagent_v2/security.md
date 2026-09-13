# Agent Security

安全边界由确定性代码实施：ToolContext 权限、Skill 工具交集、Pydantic Schema、工作区路径解析、
受保护目录、符号链接逃逸防护、写前哈希、唯一文本替换、原子写入和批次失败回滚。测试执行器只
接受 `TestRunner` 枚举；本地执行器不是任意 Shell，Docker 执行器额外关闭网络、移除 capability
并限制资源。

Git 提交权限只属于质量门禁 Orchestrator，且要求 Review APPROVED、Test PASSED 和文件哈希
一致。MCP 固定为三个只读上下文工具。所有拒绝和执行结果均进入审计；API Key、Prompt、文件
正文和敏感环境变量不会进入审计输出。
