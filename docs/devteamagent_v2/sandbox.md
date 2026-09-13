# Sandbox 与 Workspace

当前已实现的是 task project root 范围内的路径沙箱，以及可选 Docker 测试执行沙箱。
`WorkspacePathPolicy` 对解析后的真实路径做边界判断，拒绝绝对路径、`..`、敏感目录和 symlink
逃逸；File Tool 使用哈希与原子写保护并支持 Developer 批次回滚。

当前没有为每个 Task 自动创建 Git Worktree，也没有持久化 workspace_id/branch/base_commit。
因此不能把现版本描述为“每任务 Worktree 隔离”。这是后续改造项；在完成数据库迁移、运行时
启动路径和交付路径联动前，不应强行启用以免破坏非 Git 项目兼容性。
