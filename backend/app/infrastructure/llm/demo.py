from __future__ import annotations

from backend.app.domain.artifacts import (
    ArchitectureArtifact,
    DiagnosisArtifact,
    DeveloperPlan,
    PRDArtifact,
    ReviewArtifact,
    TestPlan,
    UIUXArtifact,
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
        if output_schema is UIUXArtifact:
            return output_schema.model_validate(self._ui_design(payload))
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
            "target_users": ["需要高效完成核心任务的目标用户"],
            "user_journeys": ["进入产品 → 理解价值 → 完成核心操作 → 获得清晰反馈"],
            "page_inventory": ["首页", "核心任务页", "结果与反馈页"],
            "content_strategy": ["使用真实、具体、符合业务语境的示例内容", "避免空洞占位文案"],
            "inferred_defaults": ["默认采用移动端优先的响应式设计", "默认提供加载、空白、错误和成功状态"],
        }

    @staticmethod
    def _ui_design(payload: dict) -> dict:
        prd = payload["prd"]
        return {
            "title": f"{prd['title']}：产品体验设计",
            "product_personality": ["清晰", "可信", "克制"],
            "experience_principles": [
                "每个页面只有一个明确主任务",
                "使用真实内容建立产品可信度",
                "所有操作提供及时可见的反馈",
            ],
            "options": [
                {
                    "id": "UI-OPT-01",
                    "name": "沉浸式聚焦方案",
                    "concept": "通过明确任务路径、充足留白和柔和层次减少认知负担。",
                    "mood_keywords": ["专注", "温和", "现代"],
                    "template_id": "learning-focus",
                    "palette_summary": "中性色背景配合单一蓝紫强调色",
                    "typography_summary": "大标题建立层级，正文保持舒适阅读宽度",
                    "layout_summary": "聚焦式主栏、渐进式任务步骤、即时结果反馈",
                    "advantages": ["学习路径清晰", "移动端体验稳定"],
                    "tradeoffs": ["不适合超高密度数据场景"],
                    "recommended": True,
                    "recommendation_reason": "最适合将简短需求快速转化为完整产品体验。",
                },
                {
                    "id": "UI-OPT-02",
                    "name": "专业工作台方案",
                    "concept": "使用稳定导航和模块化工作区支持更复杂的持续使用。",
                    "mood_keywords": ["专业", "理性", "高效"],
                    "template_id": "saas-workbench",
                    "palette_summary": "冷灰基础色与低饱和品牌色",
                    "typography_summary": "紧凑但清晰的多层级信息排版",
                    "layout_summary": "侧栏导航、主工作区和上下文详情面板",
                    "advantages": ["扩展性好", "适合长期功能增长"],
                    "tradeoffs": ["初始信息架构更复杂"],
                    "recommended": False,
                    "recommendation_reason": "适合功能数量较多且持续扩展的项目。",
                },
            ],
            "pages": [
                {
                    "id": "PAGE-001",
                    "name": "产品首页",
                    "purpose": "解释产品价值并引导用户进入核心任务",
                    "sections": ["价值主张", "核心入口", "最近进度", "使用说明"],
                    "primary_action": "开始核心任务",
                    "states": ["加载中", "正常内容", "空白状态", "错误状态"],
                    "responsive_behavior": ["桌面端双栏，移动端改为单栏并保持主操作可见"],
                },
                {
                    "id": "PAGE-002",
                    "name": "核心任务页",
                    "purpose": "让用户无干扰地完成主要操作并获得反馈",
                    "sections": ["任务上下文", "操作区域", "即时反馈", "下一步"],
                    "primary_action": "提交并查看结果",
                    "states": ["等待操作", "处理中", "成功反馈", "失败反馈"],
                    "responsive_behavior": ["内容宽度受控，移动端操作区固定在易触达位置"],
                },
            ],
            "component_inventory": ["导航栏", "主操作按钮", "反馈提示", "进度展示", "空状态"],
            "interaction_rules": ["主要操作必须有加载和禁用状态", "成功与失败反馈不得只依赖颜色", "键盘焦点清晰可见"],
            "accessibility_rules": ["正文不小于 16px", "文本对比度满足 WCAG AA", "交互元素具有可读名称"],
            "content_guidelines": ["使用贴近真实场景的示例数据", "按钮文案描述具体动作"],
            "icon_strategy": "使用统一线性 SVG 图标系统，不使用 Emoji 代替功能图标。",
            "asset_strategy": "只使用与业务内容相关、比例统一且来源清晰的图片或插图；无必要时以高质量排版替代装饰素材。",
            "quality_criteria": [
                "桌面、平板和手机宽度下无横向溢出",
                "首屏主标题和主要操作层级明确",
                "所有异步操作都有加载与错误反馈",
                "不使用 Emoji 替代功能图标",
                "页面不存在 lorem ipsum 或无意义占位内容",
            ],
            "tokens": {
                "colors": {
                    "background": "#F6F7FB",
                    "surface": "#FFFFFF",
                    "text": "#171923",
                    "muted": "#667085",
                    "accent": "#5B6CE1",
                },
                "typography": {
                    "display": "700 48px/1.08 Inter, system-ui",
                    "heading": "650 28px/1.2 Inter, system-ui",
                    "body": "400 16px/1.65 Inter, system-ui",
                },
                "spacing": {"xs": "8px", "sm": "12px", "md": "20px", "lg": "32px"},
                "radii": {"control": "10px", "panel": "18px"},
                "shadows": {"panel": "0 18px 55px rgba(31, 38, 70, 0.08)"},
            },
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
            "beginner_guide": (
                "用户操作首先进入应用接口，应用服务负责理解并执行操作，"
                "持久化适配器再把结果保存起来，最后由接口把处理结果返回给用户。"
            ),
            "key_concepts": [
                {
                    "name": "分层架构",
                    "plain_language_explanation": "把界面、业务处理和数据保存拆成职责不同的模块。",
                    "why_used": "修改某一层时尽量不影响其他层，也更容易测试和维护。",
                    "related_components": ["Application Service", "Persistence Adapter"],
                    "learning_hint": "先沿着一次用户请求观察数据如何经过每个模块，再分别阅读各层代码。",
                }
            ],
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
                    "interface_details": [
                        {
                            "name": "Application API",
                            "kind": "应用服务接口",
                            "purpose": "接收上层请求并调用对应业务用例。",
                            "inputs": ["操作名称", "经过校验的请求数据"],
                            "outputs": ["执行结果", "可理解的错误信息"],
                            "usage_example": "页面提交任务后，通过该接口创建并启动任务。",
                            "beginner_explanation": "它像应用服务的统一入口，上层不需要了解内部每一步是怎样实现的。",
                        }
                    ],
                },
                {
                    "name": "Persistence Adapter",
                    "responsibility": "持久化任务状态和业务产物",
                    "requirement_ids": ["NFR-001"],
                    "interfaces": ["Repository"],
                    "interface_details": [
                        {
                            "name": "Repository",
                            "kind": "数据访问接口",
                            "purpose": "统一读取和保存领域数据。",
                            "inputs": ["待保存的数据或查询条件"],
                            "outputs": ["保存结果或查询到的数据"],
                            "usage_example": "应用服务完成任务处理后，通过 Repository 保存最新状态。",
                            "beginner_explanation": "它把业务代码与具体数据库隔开，未来更换数据库时可以减少业务层改动。",
                        }
                    ],
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
        if feedback and "recommendations" in feedback:
            content += "\n## 视觉质量返工依据\n\n"
            content += "\n".join(
                f"- {recommendation}"
                for recommendation in feedback["recommendations"]
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
