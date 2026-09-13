# Model Routing

`AgentModelRouter` 使用 Agent 职责、确定性治理等级和工作流重试次数选择 LIGHT、STANDARD、
STRONG。STRICT 全部使用 STRONG；FAST 对低风险角色降档；从 Checkpoint 恢复时初始档位提升。
结构化输出失败、低 confidence 或诊断 INCONCLUSIVE 可逐级升档；连接、超时、鉴权与限流不会
做无意义的外层重复调用。

Provider 与具体模型名来自配置。LLM 不能选择任意模型，也不能修改路由规则。每次选择、升档、
完成和失败均保存原因、档位、Token 和耗时。当前尚未把实时 context_tokens 和剩余费用预算作为
首档路由输入，不能声称已完成完整的成本优化路由。
