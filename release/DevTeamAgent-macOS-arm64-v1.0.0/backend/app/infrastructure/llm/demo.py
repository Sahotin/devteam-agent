from __future__ import annotations

from pydantic import BaseModel

from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    DiagnosisArtifact,
    DeveloperPlan,
    PRDArtifact,
    ReviewArtifact,
    TestPlan,
)
from backend.app.infrastructure.llm.base import StructuredOutput


class DemoStructuredModel:
    """Deterministic local provider used until a real model adapter is configured."""

    async def generate(
        self,
        *,
        system_prompt: str,
        payload: dict,
        output_schema: type[StructuredOutput],
    ) -> StructuredOutput:
        del system_prompt
        if output_schema is PRDArtifact:
            return output_schema.model_validate(self._prd(payload))
        if output_schema is DiagnosisArtifact:
            return output_schema.model_validate(self._diagnosis(payload))
        if output_schema is ArchitectureArtifact:
            return output_schema.model_validate(self._architecture(payload))
        if output_schema is DeveloperPlan:
            return output_schema.model_validate(self._developer_plan(payload))
        if output_schema is ReviewArtifact:
            return output_schema.model_validate(self._review(payload))
        if output_schema is TestPlan:
            return output_schema.model_validate(self._test_plan(payload))
        raise ValueError(f"demo provider does not support {output_schema.__name__}")

    @staticmethod
    def _diagnosis(payload: dict) -> dict:
        report = payload["report"].strip()
        serving = "serving http on" in report.lower()
        if serving:
            return {
                "status": "USAGE_GUIDANCE",
                "summary": "当前信息未表明本地服务启动失败；该输出表示服务器正在正常监听端口。",
                "reported_symptom": report,
                "finding": "服务进程已启动，但启动命令本身不会自动打开游戏页面。",
                "root_cause": "Python http.server 是前台静态文件服务，显示 Serving HTTP 后会持续等待浏览器请求，这是正常行为。用户仍需手动访问本地地址。",
                "evidence": [
                    {
                        "source": "终端输出",
                        "observation": "出现 Serving HTTP on ... port 8080",
                        "implication": "端口监听已经建立，没有出现启动异常信息。",
                    },
                    {
                        "source": "工具行为",
                        "observation": "python -m http.server 只负责提供静态文件服务",
                        "implication": "它不会自动启动浏览器或主动展示页面。",
                    },
                ],
                "reproduction_steps": [
                    "在项目根目录启动静态文件服务",
                    "保持终端窗口运行",
                    "在浏览器访问 http://127.0.0.1:8080/index.html",
                ],
                "recommended_actions": [
                    "保持显示 Serving HTTP 的终端窗口不要关闭",
                    "手动打开浏览器并访问 http://127.0.0.1:8080/index.html",
                    "若浏览器仍无法访问，再提供浏览器错误页面和终端请求日志以继续确认",
                ],
                "requires_code_change": False,
            }
        return {
            "status": "INCONCLUSIVE",
            "summary": "仅凭当前描述无法确认软件故障，需要补充复现结果。",
            "reported_symptom": report,
            "finding": "代码检索结果尚不足以直接证明报告中的异常。",
            "root_cause": "证据不足，暂时不能可靠归因。",
            "evidence": [
                {
                    "source": "用户反馈",
                    "observation": report,
                    "implication": "说明了现象，但不是独立的复现证据。",
                }
            ],
            "reproduction_steps": ["按用户描述复现并记录完整输出"],
            "recommended_actions": ["补充错误信息、复现步骤和预期结果"],
            "requires_code_change": False,
        }

    @staticmethod
    def _prd(payload: dict) -> dict:
        requirement = payload["requirement"].strip()
        title = requirement[:60].rstrip("。.!！") or "研发任务"
        feedback = payload.get("feedback")
        assumptions = ["V1.0 优先交付最小可验证闭环"]
        if feedback:
            assumptions.append(f"本版本已根据审批反馈修订：{feedback}")
        return {
            "title": title,
            "background": f"用户提出了研发需求：{requirement}",
            "problem_statement": "需要将自然语言需求转换为可验证的软件交付结果。",
            "goals": [requirement, "确保实现结果具备明确验收标准"],
            "non_goals": ["本阶段不包含未经确认的额外功能"],
            "user_stories": [
                {
                    "id": "US-001",
                    "role": "目标用户",
                    "goal": requirement,
                    "benefit": "通过软件能力更高效地完成目标",
                }
            ],
            "requirements": [
                {
                    "id": "FR-001",
                    "description": requirement,
                    "priority": "MUST",
                },
                {
                    "id": "NFR-001",
                    "description": "实现应具备可测试性和可维护性",
                    "priority": "SHOULD",
                },
            ],
            "acceptance_criteria": [
                {
                    "id": "AC-001",
                    "requirement_ids": ["FR-001"],
                    "condition": "在满足需求约束的环境中执行核心流程",
                    "expected_result": "核心需求可以被验证且无阻断错误",
                },
                {
                    "id": "AC-002",
                    "requirement_ids": ["NFR-001"],
                    "condition": "执行项目自动化测试",
                    "expected_result": "相关测试通过并产生可追踪报告",
                },
            ],
            "assumptions": assumptions,
            "open_questions": [],
        }

    @staticmethod
    def _architecture(payload: dict) -> dict:
        prd = payload["prd"]
        feedback = payload.get("feedback")
        risks = ["真实代码结构需要在开发阶段通过代码检索进一步确认"]
        if feedback:
            risks.append(f"架构修订依据：{feedback}")
        requirement_ids = [item["id"] for item in prd["requirements"]]
        return {
            "title": f"{prd['title']}：系统架构设计",
            "overview": "采用模块化设计，将接口、业务逻辑和基础设施适配器分离。",
            "design_principles": [
                "需求到实现可追踪",
                "核心领域逻辑与基础设施解耦",
                "优先复用项目现有技术栈",
            ],
            "components": [
                {
                    "name": "Application Service",
                    "responsibility": "编排用例并维护业务边界",
                    "requirement_ids": requirement_ids,
                    "interfaces": ["Application API"],
                },
                {
                    "name": "Persistence Adapter",
                    "responsibility": "持久化任务状态和业务产物",
                    "requirement_ids": ["NFR-001"],
                    "interfaces": ["Repository"],
                },
            ],
            "data_flow": [
                "用户请求进入 API",
                "Application Service 校验并执行用例",
                "Repository 保存结果",
                "API 返回可验证结果",
            ],
            "decisions": [
                {
                    "id": "ADR-001",
                    "decision": "采用分层模块化架构",
                    "rationale": "降低领域逻辑和外部框架之间的耦合",
                    "tradeoffs": ["初始文件数量和抽象层次增加"],
                }
            ],
            "development_tasks": [
                {
                    "id": "DEV-001",
                    "title": "实现核心需求",
                    "description": prd["requirements"][0]["description"],
                    "requirement_ids": ["FR-001"],
                    "expected_files": [],
                },
                {
                    "id": "DEV-002",
                    "title": "补充自动化测试",
                    "description": "为核心需求建立验收测试",
                    "requirement_ids": ["NFR-001"],
                    "expected_files": [],
                },
            ],
            "risks": risks,
            "options": [
                {
                    "id": "ARCH-OPT-01",
                    "name": "轻量模块化方案",
                    "summary": "使用最少依赖完成核心需求，并按业务职责拆分模块。",
                    "technology_stack": ["项目现有技术栈", "模块化设计"],
                    "advantages": ["交付速度快", "部署和维护成本低"],
                    "tradeoffs": ["复杂业务增长后需要进一步拆分"],
                    "recommended": True,
                    "recommendation_reason": "最符合 V1.0 的最小可交付目标。",
                },
                {
                    "id": "ARCH-OPT-02",
                    "name": "分层扩展方案",
                    "summary": "提前划分接口层、应用层和基础设施层，为后续扩展预留边界。",
                    "technology_stack": ["项目现有技术栈", "分层架构", "依赖倒置"],
                    "advantages": ["职责边界清晰", "便于增加新能力"],
                    "tradeoffs": ["初始文件和抽象数量更多"],
                    "recommended": False,
                    "recommendation_reason": "适用于预计会持续扩展的中长期项目。",
                },
            ],
        }

    @staticmethod
    def _developer_plan(payload: dict) -> dict:
        task_id = payload["task_id"]
        architecture_version = payload["architecture_version"]
        implementation_revision = payload["implementation_revision"]
        prd = payload["prd"]
        architecture = payload["architecture"]
        feedback = payload.get("feedback")
        requirement_ids = [item["id"] for item in prd["requirements"]]
        content = (
            f"# {prd['title']}：实现计划\n\n"
            f"任务 ID：`{task_id}`\n\n"
            "## 实现摘要\n\n"
            f"{architecture['overview']}\n\n"
            "## 需求追踪\n\n"
            + "\n".join(
                f"- `{item['id']}`：{item['description']}"
                for item in prd["requirements"]
            )
            + "\n\n## 开发任务\n\n"
            + "\n".join(
                f"- `{item['id']}`：{item['title']}"
                for item in architecture["development_tasks"]
            )
            + "\n"
        )
        if feedback and "issues" in feedback:
            content += "\n## Review 返工依据\n\n"
            content += "\n".join(
                f"- `{issue['id']}`：{issue['description']}"
                for issue in feedback["issues"]
            )
            content += "\n"
        if feedback and "results" in feedback:
            content += "\n## 测试失败返工依据\n\n"
            content += "\n".join(
                f"- `{result['command_id']}`：{result['status']}"
                for result in feedback["results"]
            )
            content += "\n"
        return {
            "summary": "根据已批准架构生成可追踪的实现计划",
            "mutations": [
                {
                    "operation": "create",
                    "path": (
                        f".devteam/tasks/{task_id}/"
                        f"implementation-plan-v{architecture_version}"
                        f"-r{implementation_revision}.md"
                    ),
                    "content": content,
                    "requirement_ids": requirement_ids,
                    "reason": "为后续真实代码实现保留需求与架构追踪记录",
                }
            ],
            "verification_notes": [
                "本地 Demo Provider 只创建实现计划，不宣称业务代码已经完成"
            ],
        }

    @staticmethod
    def _review(payload: dict) -> dict:
        code_change = payload["code_change"]
        reviewed_files = [item["path"] for item in code_change["changes"]]
        return {
            "verdict": "APPROVED",
            "summary": (
                "当前变更与已批准需求和架构保持一致；自动化执行结果将在测试阶段验证"
            ),
            "reviewed_files": reviewed_files,
            "issues": [],
            "security_notes": [
                "Reviewer 使用只读工具检查变更文件，未执行任何文件修改"
            ],
        }

    @staticmethod
    def _test_plan(payload: dict) -> dict:
        acceptance_ids = [
            item["id"] for item in payload["prd"]["acceptance_criteria"]
        ]
        return {
            "summary": "执行安全的 Python 源码编译检查作为当前最小验证",
            "commands": [
                {
                    "id": "TST-001",
                    "runner": "PYTHON_COMPILE",
                    "acceptance_criteria_ids": acceptance_ids,
                    "purpose": "验证工作区内 Python 源码不存在语法错误",
                    "timeout_seconds": 60,
                }
            ],
            "limitations": [
                "Demo Provider 当前只执行编译检查，不能替代业务单元测试或集成测试"
            ],
        }
