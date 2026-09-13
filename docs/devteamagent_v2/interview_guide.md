# DevTeamAgent V2 面试指南

以下答案只描述当前代码。

1. **为什么不用 LangGraph？** 项目已有可测试的状态迁移、SQL 持久化、HITL 和恢复语义；保留自研状态机避免迁移风险，Harness 补的是单 Agent 运行治理，不是另一套业务图。
2. **Workflow 和 Harness 的区别？** Workflow 管跨角色阶段和 Artifact；Harness 管一次 Agent Run 的 Context、模型、工具、预算、停止、验证和 Trace。
3. **Harness 是什么？** 它是包住 Agent 调用的确定性运行时，统一创建 run_id、Scope、Budget、StopPolicy 和事件链。
4. **Agent Loop 怎么实现？** 当前角色内部仍是有限步骤的检索/推理/动作；Harness 观察模型和 ToolRegistry 调用并验证阶段输出，不允许递归自治循环。
5. **怎么防止无限循环？** Workflow 有返工上限；Harness 另有 Step/LLM/Tool/Repair/Token/Timeout 预算及相同工具参数、错误、上下文三连检测。
6. **HITL 在哪里？** PRD_APPROVAL、ARCH_APPROVAL、依赖安装审批及严格任务的 Harness 停止点；人决定继续、修订或终止。
7. **Checkpoint 怎么设置？** 每个合法阶段产物后保存 Workflow 状态、版本、Agent Run、Artifact 引用、Workspace、Budget、Repair 和 Context 引用。
8. **失败后怎么恢复？** `retry_failed()` 读取最近合法 Checkpoint 的数据库状态并迁移回可恢复阶段，不重新创建任务；Execution 重启时 RUNNING 回排 QUEUED。
9. **Test 失败怎么修复？** Tester 输出结构化失败证据，状态机回到 CODING，Developer 收到当前失败和历史反馈，再进入 Review/Test；轮次受上限约束。
10. **为什么最多修复 N 次？** 防止相同根因反复消耗模型；默认 Harness repair 为 3，质量门禁还保留既有的可解释人工恢复额度。
11. **Model Routing 怎么做？** Agent 职责与确定性治理等级决定首档，恢复、结构失败和低置信信号触发受控升档。
12. **为什么不让 LLM 选模型？** 模型、成本和安全边界要可复现、可审计；LLM 输出只能作为 confidence 信号。
13. **Hybrid RAG 怎么实现？** Scanner 增量索引，语言切片，BM25 和本地 Hash 向量分别排序，再用 RRF 融合。
14. **Memory 怎么实现？** SQLAlchemy 持久化短期/项目/长期记忆，写入时脱敏去重，冲突降级候选，检索时融合相关性、可信度和状态。
15. **Context Engineering 和 RAG 的区别？** RAG 只负责找到代码；Context Engineering 决定任务、Artifact、失败、RAG、Memory 等哪些内容以什么优先级进入模型预算。
16. **ToolRegistry 解决什么？** 统一注册、Schema 校验、权限校验、Skill 收窄、预算、循环检测和 ToolCall 审计。
17. **Tool Calling 是什么？** Agent 不能直接操作系统，而是用结构化输入请求 Registry 执行具名能力并获得结构化 Observation。
18. **Skill 是什么？** 可复用 SOP 加机器策略；当前 safe-code-change 规定步骤、工具、预算、停止和完成条件。
19. **Skill 和 Workflow 区别？** Skill 描述一个任务方法；Workflow 决定跨阶段何时由哪个角色运行及何时需要人工。
20. **Skill 和 MCP 区别？** Skill 是行为规范；MCP 是外部 Host 获取受控能力的标准协议。
21. **为什么 MCP 只读？** 第一版目标是统一可信 Context，写操作继续由 Host 沙箱或内部 Registry 审批，缩小远程协议攻击面。
22. **为什么 MCP 只有三个 Tool？** 它们覆盖项目上下文、代码检索和历史经验；其余能力不是第一版 Context 获取所必需。
23. **MCP 和普通 Tool Calling 区别？** MCP 规定跨进程发现与调用协议；内部 Tool Calling 是应用内调用，但最终都进入同一 ToolRegistry 安全边界。
24. **Sandbox 怎么实现？** 当前是 resolved-path 工作区边界和可选 Docker 测试沙箱；尚未实现每任务自动 Worktree。
25. **如何避免删除用户文件？** Skill/Agent 权限、路径策略、受保护目录、目标读取前置条件、哈希校验、原子写和批次回滚共同限制。
26. **为什么使用 Git Worktree？** 它能隔离并发分支和回滚，但当前仅是规划项，不能在面试中说已经实现。
27. **Multi-Agent 为什么不是越多越好？** 角色增加会扩大 Context 丢失、延迟和一致性成本；项目按产物责任拆角色，并用确定性 Workflow 串联。
28. **什么任务适合并行 Agent？** 文件集合不重叠、依赖图无边、验收可独立的子任务；当前尚未实现并行调度。
29. **如何避免文件冲突？** 当前单任务活动 Execution 互斥、哈希乐观并发控制；未来并行需 expected_files 冲突检测与 Integrator。
30. **Agent Eval 怎么做？** 结果评测看 Artifact/Gate/状态/Token/反馈，轨迹评测看 Tool 顺序、权限、重复、测试、Budget、Skill 和 Loop。
31. **评测哪些指标？** 当前有质量维度、门禁、模型用量/延迟和 trajectory score；真实成功率需实际 Runner 样本，不能臆造。
32. **轨迹如何评测？** `evaluate_trajectory()` 用 Event/ToolCall 的确定性规则扣分，不依赖 LLM Judge。
33. **如何减少幻觉？** Pydantic Structured Output、检索证据、Artifact 版本、文件实读/哈希、真实测试、Review、Gate 和审计共同约束“说完成”。
34. **如何判断真的完成？** 状态机只在所需 Review/Test/Visual Gate 和 Artifact 校验满足后进入 COMPLETED；文字自报不构成证据。
35. **与 Chatbot 最大区别？** 它有持久化任务状态、工具副作用、安全边界、结构化交付物、失败恢复与可执行质量证据。

## 最容易被继续追问的限制

主动说明：Harness 当前是“有限步骤角色 + 外层统一治理”，不是开放 ReAct；Worktree、并行 DAG、
远程 MCP、外部向量库和真实 Embedding 未实现；真实模型消融尚未运行。这种边界说明比夸大更可信。
