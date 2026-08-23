# Agent Evaluation 使用说明

DevTeam Agent 的评测分为两层，二者不能混为同一个指标：

1. **治理策略回归集**：确定性检查需求是否被分配到预期的 `FAST`、`STANDARD` 或 `STRICT`，不调用模型。
2. **真实工作流实验**：创建项目和任务，实际执行 Agent、工具、质量门禁与恢复流程，记录质量、令牌、耗时和失败证据。

## 标准数据集

`backend/app/evaluation/benchmark.py` 当前包含 21 个版本化场景，覆盖局部修改、文档维护、多页面应用、外部 API、无障碍、故障修复、部署、支付、权限、隐私数据、破坏性操作、数据迁移和技术栈迁移。

可以通过接口检查场景和确定性策略回归：

```text
GET /api/v1/evaluation/scenarios
GET /api/v1/evaluation/benchmark
```

治理策略回归通过只说明规则实现符合数据集预期，不代表真实模型能完成任务。

## 运行一次真实实验

先以需要验证的路由策略启动 DevTeam Agent：

```text
DEVTEAM_MODEL_ROUTING_STRATEGY=DYNAMIC
```

支持的实验策略：

- `DYNAMIC`：按照治理等级、Agent 职责、恢复次数和输出质量自动选档与升档。
- `FIXED_LIGHT`：所有 Agent 固定轻量档，并关闭自动升档。
- `FIXED_STANDARD`：所有 Agent 固定标准档，并关闭自动升档。
- `FIXED_STRONG`：所有 Agent 固定强档，并关闭自动升档。

服务启动后运行三个场景：

```text
python scripts/run_agent_evaluation.py run --limit 3 --name "动态路由第 1 轮"
```

只运行指定场景：

```text
python scripts/run_agent_evaluation.py run --scenario FAST-003 --scenario STANDARD-003
```

运行全部场景：

```text
python scripts/run_agent_evaluation.py run --all
```

默认只运行三个场景，避免真实模型产生未经确认的大额调用。结果保存在 `reports/generated/`，同时生成机器可读 JSON 和中文 Markdown。实验工作区保存在 `evaluation-workspace/`，两者默认不会提交到 Git。

## 对照不同路由策略

分别以 `DYNAMIC`、`FIXED_STANDARD` 等策略启动服务并对同一组场景运行评测，然后执行：

```text
python scripts/run_agent_evaluation.py compare reports/dynamic.json reports/fixed-standard.json --output reports/strategy-comparison.json
```

比较器只使用各报告共同包含的场景，分别给出：

- 工作流完成率；
- 质量门禁通过率；
- 平均任务质量分；
- 平均模型令牌；
- 模型调用耗时与完整墙钟耗时；
- 模型升档、工作流恢复和失败次数。

比较器不会用主观权重合成一个“最佳策略”。质量、成本和耗时分别排名，具体选择仍需要结合业务目标。

## 实验纪律

- 对照实验必须使用相同场景、相同代码版本和相同模型版本。
- 真实模型存在随机性，每种策略建议至少重复三轮，再报告均值和离散程度。
- Demo Provider 只验证工作流与报告链路，Token 为 0，不能用于成本结论。
- 模型网络错误、依赖缺失和代码质量失败必须分别记录，不能统一计为“Agent 能力不足”。
- 未执行过的结果不得填写到简历；简历中的成功率、成本和耗时必须来自保留的实验报告。
