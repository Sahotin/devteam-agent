# DevTeam Agent v0.9 实现说明

## 1. 本版本目标

v0.9 为 DevTeam Agent 增加可实际操作的 React 研发工作台。它不是静态项目展示页，而是现有 Orchestrator、Execution、SSE、Artifact、ToolCall 和 Memory 能力的统一交互入口。

本版本主要解决以下问题：

- 用户此前必须通过 Swagger 或脚本逐个调用工作流接口。
- 任务状态、Agent 进度、工具执行与产物引用分散在多个 API 中。
- PRD 和架构审批缺少适合真实研发流程的交互界面。
- SSE 已经提供实时事件，但还没有前端消费和断线状态反馈。

## 2. 前端技术架构

前端使用 React 19、TypeScript、Vite 和原生 CSS，目录位于 `frontend/`。

主要分层如下：

```text
App
├── api/
│   ├── client.ts        HTTP 与 SSE 地址封装
│   └── types.ts         后端领域模型镜像
├── hooks/
│   └── useTaskStream    SSE 生命周期与重连状态
├── lib/
│   └── workflow         状态映射、动作选择与展示规则
├── components/
│   ├── Sidebar          项目与任务导航
│   ├── WorkflowRail     状态机进度
│   ├── Timeline         领域事件时间线
│   └── Inspector        Artifact/Tool/Memory 审计
└── App.tsx              数据协调和工作流操作
```

前端没有复制 Orchestrator 状态迁移逻辑。界面只根据当前 Task State 决定允许用户提交哪一种受控 Execution Action，最终合法性仍由后端状态机和数据库约束判断。

## 3. 项目与任务管理

工作台启动后通过集合接口读取全部项目，并按当前项目读取任务：

- `GET /api/v1/projects`
- `GET /api/v1/tasks?project_id=...`

本版本补充了对应 Repository 和 FastAPI 接口。项目创建表单采集名称、仓库绝对路径和概述；任务表单采集自然语言需求。创建成功后自动切换到新对象，不需要手动刷新。

任务导航显示当前状态、状态版本和需求摘要。项目切换会同步刷新任务和 Project Memory，并清理不属于新项目的旧选中状态。

## 4. 工作流控制面板

工作台根据任务状态提供最小操作集合：

- `CREATED`：启动 Agent 团队。
- `PRD_APPROVAL`：批准 PRD 或携带反馈要求修改。
- `ARCH_APPROVAL`：批准架构或携带反馈要求修改。
- `REVIEWING`：触发独立代码审查。
- `TESTING`：触发测试验证。
- `PAUSED`：恢复任务。
- 可暂停状态：暂停任务。
- 非终态且无活动 Execution：取消任务。

所有长流程操作统一提交到 `POST /tasks/{id}/executions`，界面展示 Execution ID、Action、Status 和 Attempt。任务已有 `QUEUED` 或 `RUNNING` 作业时，工作流按钮会禁用，数据库唯一约束仍作为最终并发保护。

取消任务会显示明确确认提示。运行中的 Execution 不通过前端伪装成已取消，保持 v0.8 的后端治理边界。

## 5. 状态机可视化

WorkflowRail 将研发过程映射为七个主要交付阶段：任务创建、需求分析、系统设计、代码实现、代码审查、测试验证和交付完成。

每个阶段显示负责 Agent，并区分：

- 已完成阶段。
- 当前活动阶段。
- 尚未开始阶段。
- 暂停、失败或取消后的停止状态。

状态计算是纯函数并包含单元测试，避免视觉进度把未来阶段错误标记为完成。

## 6. SSE 实时同步

`useTaskStream` 为当前任务创建 EventSource，并监听任务、Artifact、Tool 和 Execution 事件。收到事件后执行两层更新：

1. 立即将新 Event 去重后追加到时间线。
2. 以短延迟合并高频刷新，重新获取 Observability、任务列表和项目记忆。

界面显示 `连接中`、`实时同步` 和 `正在重连` 三种连接状态。EventSource 原生负责断线重连，持久化事件则保证重新连接时能够恢复历史。

## 7. 可观测与审计界面

主界面展示四个实时指标：Artifact、ToolCall、Verified Memory 和 Event 数量。

右侧 Inspector 提供三个视图：

### 7.1 Artifact

按类型和版本切换 PRD、Architecture、CodeChange、Review 和 TestReport。默认展示字段摘要，并允许展开原始结构化 JSON。Artifact 中的 `memory_ids`、`searched_context` 等引用保持可见。

### 7.2 ToolCall

显示工具名称、调用 Agent、成功或失败状态、时间和错误信息。工具输入输出仍遵循后端审计脱敏策略，前端不尝试获取被后端隐藏的敏感正文。

### 7.3 Memory

显示 Short-term、Project、Long-term Memory 的状态、分类、摘要、正文、可信度和有效性。Verified、Stale 和 Rejected 使用不同状态色。

## 8. 视觉与交互设计

工作台采用高密度深色研发控制台风格，使用低饱和表面、细边框和荧光绿色表达有效状态；Amber、Red、Blue 和 Violet 分别区分审批、失败、工具与状态事件。

布局包含：

- 左侧项目与任务导航。
- 顶部任务标题、状态和 SSE 连接状态。
- 中央交付 Pipeline 与操作台。
- 下方 Agent 时间线和审计 Inspector。

CSS 提供 1100px 和 760px 两级响应式布局、键盘焦点样式、按钮禁用状态和 `prefers-reduced-motion` 支持。界面不依赖外部图片或在线字体，离线环境也能完整渲染。

## 9. 开发与生产集成

开发模式下，Vite 将 `/api` 代理到 `127.0.0.1:8000`，HTTP 与 SSE 使用同源相对路径。

生产构建输出到 `frontend/dist`。FastAPI 启动时检测该目录；若存在，则在 API Router 之后挂载静态文件服务，因此单个后端进程可以同时提供 API 和前端工作台。

后端还增加了可配置 CORS：

- `DEVTEAM_CORS_ORIGINS`
- 默认允许 `localhost:5173` 和 `127.0.0.1:5173`

## 10. 自动化验证

本版本完成以下验证：

- 后端自动化测试 48 项全部通过。
- 前端工作流纯函数测试 3 项全部通过。
- TypeScript 项目引用和严格类型检查通过。
- Vite 生产构建通过。
- Python 源码编译检查通过。
- Python 依赖一致性检查通过。

前端测试覆盖状态到动作映射、Pipeline 阶段进度和中文状态展示。后端新增集合接口测试覆盖项目列表、按项目查询任务及不存在项目的错误响应。

## 11. 当前边界与下一步

当前工作台是单页应用，尚未包含账号体系、团队权限和多用户审批归属。Artifact 的 JSON 展开已经支持完整审计，但 PRD、架构图、Diff 和测试报告仍可以增加专用渲染组件。

下一阶段建议进入 v1.0 工程化交付：增加真实 LLM Provider、配置与密钥治理、结构化日志和指标、Docker Compose 一键启动、数据库迁移，以及端到端演示脚本和最终验收报告。

