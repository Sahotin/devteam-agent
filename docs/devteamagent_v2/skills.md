# Skills

`skills/safe-code-change/SKILL.md` 是 Codex Host 可发现的指令文件；同目录 `skill.yaml` 是
DevTeam 运行时的机器可校验定义。`SkillRegistry` 启动时解析并验证名称、版本、SOP、工具白名单、
前后置条件、停止规则和预算。

当前只实现 `safe-code-change`。Developer Run 使用该 Skill，工具集合取 Agent 白名单与 Skill
白名单的交集。服务端强制：写入前至少完成一次成功的 `code.search` 或 `rag.search`；替换或删除
已有文件前必须成功读取或检查同一路径。违反规则会被拒绝并写入 ToolCall 审计。

完整 SOP 是理解任务、搜索代码、获取上下文、最小修改、测试、Diff Review 和验证。宏观的
Review/Test/Postcondition 仍由现有 Workflow 质量门禁完成，不能把单次 Developer 阶段结束
误解为整个 Skill 已通过。
