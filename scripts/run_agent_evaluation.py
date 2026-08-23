from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from backend.app.domain.enums import GovernanceLevel
from backend.app.evaluation.benchmark import EvaluationScenario
from backend.app.evaluation.experiment import (
    AgentEvaluationExperimentReport,
    AgentEvaluationResult,
    StrategyComparisonReport,
    build_experiment_report,
    compare_experiments,
)


DATASET_VERSION = "AGENT_DATASET_V1"
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}
ACTION_SEQUENCE = (
    {"action": "START"},
    {"action": "DECIDE_PRD", "decision": "APPROVED"},
    {
        "action": "DECIDE_ARCHITECTURE",
        "decision": "APPROVED",
        "autonomous": True,
    },
    {"action": "RUN_REVIEW"},
    {"action": "RUN_TESTS"},
)


class ApiClient:
    def __init__(
        self,
        api_root: str,
        *,
        request_timeout: float,
        execution_timeout: float,
        poll_interval: float,
    ) -> None:
        self.api_root = api_root.rstrip("/")
        self.request_timeout = request_timeout
        self.execution_timeout = execution_timeout
        self.poll_interval = poll_interval

    def request(self, method: str, path: str, payload: dict | None = None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.api_root}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.request_timeout) as response:
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"{method} {path} 返回 {error.code}：{detail}"
            ) from error
        except URLError as error:
            raise RuntimeError(f"无法连接 DevTeam Agent：{error.reason}") from error

    def run_action(self, task_id: str, payload: dict) -> dict:
        execution = self.request(
            "POST", f"/tasks/{task_id}/executions", payload
        )
        deadline = time.monotonic() + self.execution_timeout
        while time.monotonic() < deadline:
            current = self.request("GET", f"/executions/{execution['id']}")
            if current["status"] == "SUCCEEDED":
                return current
            if current["status"] in {"FAILED", "CANCELLED"}:
                raise RuntimeError(
                    f"动作 {payload['action']} {current['status']}："
                    f"{current.get('error_message') or '没有错误详情'}"
                )
            time.sleep(self.poll_interval)
        raise TimeoutError(
            f"动作 {payload['action']} 在 {self.execution_timeout:g} 秒内未完成"
        )


def run_experiment(args: argparse.Namespace) -> AgentEvaluationExperimentReport:
    client = ApiClient(
        args.api_root,
        request_timeout=args.request_timeout,
        execution_timeout=args.execution_timeout,
        poll_interval=args.poll_interval,
    )
    ready = client.request("GET", "/ready")
    if ready != {"status": "ready"}:
        raise RuntimeError(f"服务尚未就绪：{ready}")
    capabilities = client.request("GET", "/capabilities")
    strategy = str(capabilities["model_routing_strategy"])
    scenarios = [
        EvaluationScenario.model_validate(item)
        for item in client.request("GET", "/evaluation/scenarios")
    ]
    selected = select_scenarios(
        scenarios,
        scenario_ids=args.scenario,
        category=args.category,
        limit=None if args.all else args.limit,
    )
    if not selected:
        raise ValueError("没有符合筛选条件的评测场景")

    experiment_id = uuid4().hex
    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    results: list[AgentEvaluationResult] = []
    for index, scenario in enumerate(selected, start=1):
        print(f"[{index}/{len(selected)}] {scenario.id} · {scenario.category}")
        results.append(
            run_scenario(
                client,
                scenario,
                workspace / experiment_id / scenario.id.lower(),
            )
        )
    finished_at = datetime.now(timezone.utc)
    report = build_experiment_report(
        experiment_id=experiment_id,
        name=args.name or f"{strategy} 路由实验",
        dataset_version=DATASET_VERSION,
        routing_strategy=strategy,
        provider=str(capabilities["llm_provider"]),
        model=(
            "demo-structured-model"
            if capabilities["llm_provider"] == "demo"
            else str(capabilities["llm_model"])
        ),
        started_at=started_at,
        finished_at=finished_at,
        results=results,
        limitations=[
            "评测任务会真实调用当前配置的模型与工具，结果受模型随机性和本机环境影响。",
            "固定档策略关闭自动升档，仅用于与动态路由进行可重复的对照实验。",
            "Demo Provider 用于验证流水线，不产生可代表真实模型质量的令牌成本结论。",
        ],
    )
    output = output_path(args.output, strategy, experiment_id, "json")
    write_text(output, report.model_dump_json(indent=2))
    markdown = output.with_suffix(".md")
    write_text(markdown, render_experiment_markdown(report))
    print(f"评测报告：{output}")
    print(f"可读报告：{markdown}")
    return report


def run_scenario(
    client: ApiClient,
    scenario: EvaluationScenario,
    workspace: Path,
) -> AgentEvaluationResult:
    workspace.mkdir(parents=True, exist_ok=True)
    task_id: str | None = None
    error_message: str | None = None
    started = time.perf_counter()
    task: dict = {}
    try:
        project = client.request(
            "POST",
            "/projects",
            {
                "name": f"Agent Eval · {scenario.id}",
                "root_path": str(workspace),
                "summary": f"{DATASET_VERSION} 自动评测场景",
            },
        )
        task = client.request(
            "POST",
            "/tasks",
            {
                "project_id": project["id"],
                "requirement": scenario.requirement,
                "execution_scope": scenario.execution_scope.value,
                "preference": scenario.preference.value,
            },
        )
        task_id = str(task["id"])
        for action in ACTION_SEQUENCE:
            task = client.request("GET", f"/tasks/{task_id}")
            if task["state"] in TERMINAL_STATES:
                break
            client.run_action(task_id, action)
        task = client.request("GET", f"/tasks/{task_id}")
    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"

    wall_time_ms = max(0, round((time.perf_counter() - started) * 1000))
    return collect_result(
        client,
        scenario,
        task_id=task_id,
        task=task,
        wall_time_ms=wall_time_ms,
        error_message=error_message,
    )


def collect_result(
    client: ApiClient,
    scenario: EvaluationScenario,
    *,
    task_id: str | None,
    task: dict,
    wall_time_ms: int,
    error_message: str | None,
) -> AgentEvaluationResult:
    actual = None
    policy = task.get("policy") if isinstance(task, dict) else None
    if policy and policy.get("governance_level"):
        actual = GovernanceLevel(policy["governance_level"])
    if task_id is None:
        return AgentEvaluationResult(
            scenario_id=scenario.id,
            category=scenario.category,
            expected_governance=scenario.expected_governance,
            wall_time_ms=wall_time_ms,
            error=error_message,
        )

    try:
        evaluation = client.request("GET", f"/tasks/{task_id}/evaluation")
        observation = client.request("GET", f"/tasks/{task_id}/observability")
        events = client.request("GET", f"/tasks/{task_id}/events")
        usage = evaluation["model_usage"]
        recoveries = sum(
            event["event_type"] == "task.state_changed"
            and event["payload"].get("retry") is True
            for event in events
        )
        failures = sum(
            event["event_type"] == "task.state_changed"
            and event["payload"].get("to") == "FAILED"
            for event in events
        )
        return AgentEvaluationResult(
            scenario_id=scenario.id,
            category=scenario.category,
            task_id=task_id,
            expected_governance=scenario.expected_governance,
            actual_governance=actual,
            governance_match=actual is scenario.expected_governance,
            completed=task.get("state") == "COMPLETED",
            quality_gate_passed=bool(evaluation["quality_gate_passed"]),
            overall_score=int(evaluation["overall_score"]),
            total_tokens=int(usage["total_tokens"]),
            model_attempts=int(usage["attempts"]),
            model_escalations=int(usage["escalations"]),
            model_failures=int(usage["failed_calls"]),
            model_latency_ms=int(usage["latency_ms"]),
            wall_time_ms=wall_time_ms,
            tool_calls=int(observation["tool_call_count"]),
            event_count=int(observation["event_count"]),
            workflow_recoveries=recoveries,
            workflow_failures=failures,
            error=error_message or task.get("error_message"),
        )
    except Exception as collection_error:
        return AgentEvaluationResult(
            scenario_id=scenario.id,
            category=scenario.category,
            task_id=task_id,
            expected_governance=scenario.expected_governance,
            actual_governance=actual,
            governance_match=actual is scenario.expected_governance,
            completed=task.get("state") == "COMPLETED",
            wall_time_ms=wall_time_ms,
            error=(
                error_message
                or f"结果采集失败：{type(collection_error).__name__}: {collection_error}"
            ),
        )


def select_scenarios(
    scenarios: list[EvaluationScenario],
    *,
    scenario_ids: list[str] | None,
    category: str | None,
    limit: int | None,
) -> list[EvaluationScenario]:
    selected = scenarios
    if scenario_ids:
        requested = {item.upper() for item in scenario_ids}
        selected = [item for item in selected if item.id.upper() in requested]
    if category:
        selected = [item for item in selected if item.category == category]
    return selected if limit is None else selected[: max(0, limit)]


def compare_reports(args: argparse.Namespace) -> StrategyComparisonReport:
    reports = [
        AgentEvaluationExperimentReport.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )
        for path in args.reports
    ]
    comparison = compare_experiments(reports)
    output = Path(args.output).expanduser().resolve()
    write_text(output, comparison.model_dump_json(indent=2))
    write_text(output.with_suffix(".md"), render_comparison_markdown(comparison))
    print(f"对照报告：{output}")
    return comparison


def render_experiment_markdown(report: AgentEvaluationExperimentReport) -> str:
    aggregate = report.aggregate
    rows = [
        "# DevTeam Agent 评测报告",
        "",
        f"- 实验：{report.name}",
        f"- 路由策略：{report.routing_strategy}",
        f"- 模型：{report.provider} / {report.model}",
        f"- 数据集：{report.dataset_version}",
        f"- 场景数：{aggregate.scenario_count}",
        f"- 完成率：{aggregate.completion_rate:.2f}%",
        f"- 质量门禁通过率：{aggregate.quality_gate_pass_rate:.2f}%",
        f"- 平均质量分：{aggregate.average_score:.2f}",
        f"- 平均令牌：{aggregate.average_tokens:.2f}",
        f"- 平均墙钟耗时：{aggregate.average_wall_time_ms:.2f} ms",
        "",
        "| 场景 | 类别 | 治理匹配 | 完成 | 门禁 | 得分 | 令牌 | 耗时(ms) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for result in report.results:
        rows.append(
            f"| {result.scenario_id} | {result.category} | "
            f"{'是' if result.governance_match else '否'} | "
            f"{'是' if result.completed else '否'} | "
            f"{'是' if result.quality_gate_passed else '否'} | "
            f"{result.overall_score if result.overall_score is not None else '-'} | "
            f"{result.total_tokens} | {result.wall_time_ms} |"
        )
    rows.extend(["", "## 限制说明", ""])
    rows.extend(f"- {item}" for item in report.limitations)
    return "\n".join(rows) + "\n"


def render_comparison_markdown(report: StrategyComparisonReport) -> str:
    rows = [
        "# DevTeam Agent 模型路由对照报告",
        "",
        f"共同场景：{len(report.comparable_scenarios)} 个",
        "",
        "| 策略 | 完成率 | 门禁通过率 | 平均分 | 平均令牌 | 平均耗时(ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report.rows:
        metric = row.aggregate
        rows.append(
            f"| {row.routing_strategy} | {metric.completion_rate:.2f}% | "
            f"{metric.quality_gate_pass_rate:.2f}% | {metric.average_score:.2f} | "
            f"{metric.average_tokens:.2f} | {metric.average_wall_time_ms:.2f} |"
        )
    rows.extend(
        [
            "",
            f"- 最高平均质量：{report.highest_quality_strategy or '无'}",
            f"- 最低平均令牌：{report.lowest_token_strategy or '无'}",
            f"- 最低平均耗时：{report.fastest_strategy or '无'}",
            "",
        ]
    )
    rows.extend(f"- {item}" for item in report.notes)
    return "\n".join(rows) + "\n"


def output_path(
    configured: str | None,
    strategy: str,
    experiment_id: str,
    suffix: str,
) -> Path:
    if configured:
        return Path(configured).expanduser().resolve()
    return (
        Path("reports/generated")
        / f"agent-evaluation-{strategy.lower()}-{experiment_id[:8]}.{suffix}"
    ).resolve()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="DevTeam Agent 可复现评测运行器")
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="运行一批真实工作流场景")
    run.add_argument(
        "--api-root",
        default=os.getenv("DEVTEAM_API_ROOT", "http://127.0.0.1:8000/api/v1"),
    )
    run.add_argument("--workspace", default="evaluation-workspace")
    run.add_argument("--output")
    run.add_argument("--name")
    run.add_argument("--scenario", action="append")
    run.add_argument("--category")
    run.add_argument("--limit", type=int, default=3)
    run.add_argument("--all", action="store_true")
    run.add_argument("--request-timeout", type=float, default=20)
    run.add_argument("--execution-timeout", type=float, default=300)
    run.add_argument("--poll-interval", type=float, default=0.5)

    compare = commands.add_parser("compare", help="比较至少两份实验 JSON 报告")
    compare.add_argument("reports", nargs="+")
    compare.add_argument("--output", default="reports/strategy-comparison.json")
    return root


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    if args.command == "run":
        run_experiment(args)
    else:
        compare_reports(args)


if __name__ == "__main__":
    main()
