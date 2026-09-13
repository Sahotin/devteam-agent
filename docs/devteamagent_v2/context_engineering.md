# Context Engineering

`ContextItem` 记录 source、source_id、content、priority、token_count、relevance、version 和时间。
`ContextBuilder` 按优先级、相关度、时间和稳定 ID 确定性选择内容。每个 Harness Run 使用
`DEVTEAM_AGENT_CONTEXT_MAX_TOKENS` 作为上限。

模型请求的当前任务、故障证据和核心 PRD/CodeChange/Review 被视为必需上下文；架构、代码检索、
Memory 和历史信息按较低优先级进入剩余预算。被裁剪的顶层来源写入 `CONTEXT_BUILT`，不会记录
正文。若必需上下文本身超限，会明确记录 `required_context_overflow`，而不是静默删除当前任务。

Developer 原有的细粒度限制仍保留：检索正文总量 24,000 字符、Memory 最多 3 条且单条
1,200 字符、最近反馈 3 份。V2 的统一 Builder 是外层预算，原限制是 Developer 内部的防膨胀
策略，两者作用层级不同。
