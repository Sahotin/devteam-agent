# Evaluation

现有质量评测继续基于 Artifact、状态、模型用量和用户反馈。V2 新增
`evaluate_trajectory()`，完全使用 Event 与 ToolCall 的确定性证据，检查：修改前是否检索、修改后
是否测试、非法/未授权工具、三连重复调用、Skill 违反、Budget 超限和 Loop Detection。

轨迹评分是可解释的规则分，不是模型能力的统计结论。没有运行真实模型对照实验时，不报告成功率、
Token 节省或质量提升。现有 21 个策略场景和 Agent Evaluation Runner 可继续用于后续真实对照。
