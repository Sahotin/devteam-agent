# DevTeamAgent V2 Upgrade Report

## 1. Repository Audit

项目是 FastAPI/React 前后端、SQLAlchemy 持久化、结构化 Artifact 驱动的自定义状态机系统。
完整审计见 `repository_audit.md`。

## 2. Architecture Changes

保留 WorkflowService，新增 `backend/app/agent_runtime/` 作为所有执行角色外层 Harness；复用
RoutedStructuredModel、ToolRegistry、Event、Checkpoint、RAG、Memory 和既有质量门禁。

## 3. Agent Harness

实现 Run Scope、八种运行状态、硬 Budget、StopPolicy、ContextBuilder、Skill 策略、验证事件与
错误/HITL 路由。7 个 LLM Agent 和 Visual Reviewer 的阶段入口均已接入。

## 4. Skills

`safe-code-change` 同时具有 Host `SKILL.md` 和运行时 `skill.yaml`。工具白名单取交集，服务端
强制写前搜索、替换/删除前读取目标。

## 5. MCP

复用已实现的真实 stdio MCP，只暴露 project context、Hybrid code search、memory query；固定
身份和权限，全部经过 ToolRegistry。版本更新为 2.0.0。

## 6. Sandbox

已有 resolved-path 工作区沙箱、敏感目录/symlink 逃逸防护、原子写、哈希和回滚；Terminal
只接受枚举测试命令并可选 Docker 隔离。每任务自动 Git Worktree 尚未实现。

## 7. Context Engineering

统一 ContextItem/Builder 按当前任务、失败、Artifact、代码、Memory、历史的优先级在 Token
预算内选取顶层来源，并记录来源而非正文。Developer 的细粒度 Chunk/Memory/反馈限制继续保留。

## 8. Model Routing

首档由角色和治理等级决定；Checkpoint 重试、repair_round >= 2、Context 达到配置阈值会确定性
升档，低置信/结构错误可继续升档。网络、超时、鉴权和限流不做外层重复调用。

## 9. Dynamic Workflow

当前真实动态性来自 ExecutionScope 和 BUG_FIX 诊断分支。尚未实现通用 TaskClassifier/DAG，
因此不将其宣传为任意动态工作流。

## 10. Parallel Agent

未实现。当前没有持久化 SubTask 依赖图、expected_files 冲突调度和 Integrator；为避免“假并发”
和破坏文件一致性，本次没有添加未接线的并行类。

## 11. Checkpoint / Recovery

Checkpoint 新增 workflow/agent state、run_id、任务、当前步骤、全量 Artifact 引用、Workspace、
Budget usage、repair round 和 context refs。既有 retry_failed 从最近合法状态恢复；严格任务 Harness
停止可进入 PAUSED/HITL。

## 12. Trace

新增 Agent、Context、LLM、Tool、Verify、Run、HITL、Checkpoint 事件，复用数据库 Event 与 SSE。
不记录 Prompt、业务正文、文件正文、API Key 或隐藏思维。

## 13. Eval

EVAL_V2 新增确定性 trajectory score，检查写前检索、写后测试、无效/未授权调用、重复调用、
Skill、Budget 和 Loop。没有运行真实模型消融，不报告虚构提升。

## 14. Database Changes

没有新增表或破坏迁移；扩展内容存入现有 JSON Checkpoint/Event，保持旧数据库兼容。

## 15. API Changes

现有 API 保持兼容；任务 Evaluation 响应新增可空 `trajectory` 字段。

## 16. Frontend Changes

现有 Evaluation Panel 新增确定性 Agent 工具轨迹分数和违规说明；Timeline 自动展示新增事件。

## 17. Tests

- Backend：186 passed，0 failed，0 skipped；branch coverage 81.92%，门禁 80%。
- Frontend：6 files / 32 tests passed；TypeScript typecheck 与 production build 通过。
- Playwright：1 个完整浏览器工作流通过（需求提交至最终交付）。
- MCP、权限、路径、Terminal、事务回滚、Harness、Budget、Loop、Skill、Context、Checkpoint、
  ModelRouter 和 Evaluation 均包含回归覆盖。
- Demo Provider 真实 REST/Execution/SSE 持久化链路冒烟通过：7 Artifacts、10 ToolCalls、173 Events。

## 18. Commands

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
.\.venv\Scripts\python.exe -m pytest --cov=backend.app --cov-fail-under=80 backend/tests
pnpm --dir frontend typecheck
pnpm --dir frontend test
pnpm --dir frontend build
$env:DEVTEAM_MCP_ALLOWED_WORKSPACE_ROOTS='C:\path\to\allowed'; .\.venv\Scripts\devteam-mcp.exe
```

服务启动后执行 `.\.venv\Scripts\python.exe scripts/smoke_test.py`。MCP Inspector 可执行
`npx @modelcontextprotocol/inspector .\.venv\Scripts\devteam-mcp.exe`。

## 19. Remaining Risks

每任务 Worktree、通用 DAG、并行 Agent、Child Run Replay、远程 MCP/OAuth、外部向量库、真实
Embedding 和真实模型消融未实现。本地 Terminal 的隔离弱于 Docker。ContextBuilder 当前按顶层
字段裁剪，复杂 Artifact 内部仍依赖各 Agent 的专用压缩逻辑。

## 20. Interview Highlights

1. Workflow 与 Harness 分层；2. 确定性状态机 + Agentic 推理；3. 硬 Budget；4. Loop Detection；
5. 机器可执行 Skill；6. MCP 与 ToolRegistry 统一安全边界；7. Hybrid RAG/Memory Context；
8. 哈希、原子写和回滚；9. Checkpoint 恢复；10. SSE Trace；11. 确定性 Trajectory Eval；
12. 明确说明未实现边界并拒绝虚构 Benchmark。
