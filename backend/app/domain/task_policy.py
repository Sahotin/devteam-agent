from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.enums import (
    DeliveryPreference,
    ExecutionScope,
    GovernanceLevel,
)


class TaskPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_scope: ExecutionScope
    preference: DeliveryPreference
    risk_score: int = Field(ge=0)
    governance_level: GovernanceLevel
    reasons: list[str]
    hard_risk_flags: list[str]
    assessed_at: datetime


class TaskComplexityPolicy:
    """确定性任务评分器：结果可解释、可复现，也便于质量回归。"""

    _IGNORED_DIRECTORIES = {
        ".git",
        ".devteam",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "__pycache__",
    }
    _CODE_SUFFIXES = {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".go",
        ".rs",
        ".cs",
        ".cpp",
        ".c",
        ".vue",
        ".svelte",
    }
    _HARD_RISKS = (
        (
            "PAYMENT",
            re.compile(r"支付|退款|交易|结算|payment|refund", re.IGNORECASE),
            "涉及支付、退款或交易链路",
        ),
        (
            "AUTHORIZATION",
            re.compile(
                r"登录|认证|授权|权限|密码|oauth|token|permission|authentication",
                re.IGNORECASE,
            ),
            "涉及身份认证、权限或凭据安全",
        ),
        (
            "DATA_MIGRATION",
            re.compile(
                r"数据库迁移|表结构|数据迁移|schema\s+migration|alter\s+table|drop\s+table",
                re.IGNORECASE,
            ),
            "涉及数据库结构或数据迁移",
        ),
        (
            "DESTRUCTIVE_DATA",
            re.compile(
                r"删除.{0,8}(数据|记录|文件)|清空.{0,8}(数据|数据库)|不可逆|永久删除",
                re.IGNORECASE,
            ),
            "包含可能不可逆的数据删除操作",
        ),
        (
            "SENSITIVE_DATA",
            re.compile(
                r"隐私|敏感数据|个人信息|身份证|银行卡|手机号|privacy|pii",
                re.IGNORECASE,
            ),
            "涉及隐私或敏感个人数据",
        ),
    )

    def assess(
        self,
        *,
        requirement: str,
        execution_scope: ExecutionScope,
        preference: DeliveryPreference,
        workspace_root: str | None = None,
    ) -> TaskPolicyDecision:
        normalized = " ".join(requirement.split())
        score = 0
        reasons: list[str] = []
        hard_risk_flags: list[str] = []

        for flag, pattern, reason in self._HARD_RISKS:
            if pattern.search(normalized):
                hard_risk_flags.append(flag)
                reasons.append(reason + "，强制采用严格治理")

        score = self._add_text_risks(normalized, score, reasons)
        code_files = self._count_code_files(workspace_root)
        if code_files >= 20:
            score += 2
            reasons.append("现有仓库包含较多代码文件，修改需要跨文件一致性检查（+2）")
        elif code_files > 0:
            score += 1
            reasons.append("检测到已有代码仓库，需要保护现有实现（+1）")

        governance = self._governance_for(
            score=score,
            preference=preference,
            execution_scope=execution_scope,
            has_hard_risk=bool(hard_risk_flags),
        )
        if not reasons:
            reasons.append("需求范围较小且未识别到高风险因素")
        reasons.append(
            self._decision_reason(score, preference, governance, hard_risk_flags)
        )
        return TaskPolicyDecision(
            execution_scope=execution_scope,
            preference=preference,
            risk_score=score,
            governance_level=governance,
            reasons=reasons,
            hard_risk_flags=hard_risk_flags,
            assessed_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _add_text_risks(text: str, score: int, reasons: list[str]) -> int:
        rules = (
            (
                3,
                r"重构|核心逻辑|架构调整|架构升级|微服务|跨模块|全局修改|rewrite",
                "涉及架构、核心逻辑或跨模块变更（+3）",
            ),
            (
                3,
                r"新框架|新技术栈|替换框架|框架迁移|技术栈迁移",
                "涉及新框架或技术栈迁移（+3）",
            ),
            (
                2,
                r"第三方.{0,8}(接口|API|服务)|外部服务|webhook|回调接口",
                "涉及第三方服务集成（+2）",
            ),
            (
                2,
                r"多页面|多个页面|多个模块|前后端|管理后台|完整平台",
                "需求覆盖多个页面或模块（+2）",
            ),
            (
                2,
                r"新增|增加|实现|开发|创建|接入|支持",
                "包含新增功能开发（+2）",
            ),
            (
                1,
                r"修改|修复|优化|调整|更新",
                "需要修改已有行为（+1）",
            ),
            (
                1,
                r"部署|发布|生产环境|容器|docker|kubernetes",
                "涉及部署或运行环境（+1）",
            ),
        )
        for weight, pattern, reason in rules:
            if re.search(pattern, text, re.IGNORECASE):
                score += weight
                reasons.append(reason)
        if re.search(r"重构|核心逻辑|架构调整|架构升级", text, re.IGNORECASE) and re.search(
            r"跨模块|多个模块|前后端|全局修改", text, re.IGNORECASE
        ):
            score += 2
            reasons.append("核心或架构变更同时跨越多个模块，一致性风险叠加（+2）")
        if len(text) > 1200:
            score += 2
            reasons.append("需求描述较长，包含较多约束（+2）")
        elif len(text) > 400:
            score += 1
            reasons.append("需求描述包含多项约束（+1）")
        if re.search(r"优化一下|完善一下|做一个|类似于|看着办|自主设计", text):
            score += 2
            reasons.append("需求存在开放性或歧义，需要补充分析（+2）")
        return score

    @staticmethod
    def _governance_for(
        *,
        score: int,
        preference: DeliveryPreference,
        execution_scope: ExecutionScope,
        has_hard_risk: bool,
    ) -> GovernanceLevel:
        if has_hard_risk:
            return GovernanceLevel.STRICT
        if preference is DeliveryPreference.QUALITY:
            governance = (
                GovernanceLevel.FAST
                if score <= 1
                else GovernanceLevel.STANDARD
                if score <= 6
                else GovernanceLevel.STRICT
            )
        elif preference is DeliveryPreference.ECONOMY:
            governance = (
                GovernanceLevel.FAST
                if score <= 4
                else GovernanceLevel.STANDARD
                if score <= 9
                else GovernanceLevel.STRICT
            )
        else:
            governance = (
                GovernanceLevel.FAST
                if score <= 3
                else GovernanceLevel.STANDARD
                if score <= 8
                else GovernanceLevel.STRICT
            )
        if (
            execution_scope is ExecutionScope.REVIEW_ONLY
            and governance is GovernanceLevel.FAST
        ):
            return GovernanceLevel.STANDARD
        return governance

    @staticmethod
    def _decision_reason(
        score: int,
        preference: DeliveryPreference,
        governance: GovernanceLevel,
        hard_risk_flags: list[str],
    ) -> str:
        if hard_risk_flags:
            return "命中硬风险规则，用户的成本偏好不能降低治理等级"
        return (
            f"综合风险分数为 {score}，结合 {preference.value} 偏好，"
            f"选择 {governance.value} 治理等级"
        )

    @classmethod
    def _count_code_files(cls, workspace_root: str | None) -> int:
        if not workspace_root:
            return 0
        root = Path(workspace_root).expanduser().resolve()
        if not root.is_dir():
            return 0
        count = 0
        visited_entries = 0
        pending = deque([root])
        while pending and count < 20 and visited_entries < 1000:
            directory = pending.popleft()
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                visited_entries += 1
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir():
                        if entry.name not in cls._IGNORED_DIRECTORIES:
                            pending.append(entry)
                    elif entry.suffix.lower() in cls._CODE_SUFFIXES:
                        count += 1
                        if count >= 20:
                            break
                except OSError:
                    continue
                if visited_entries >= 1000:
                    break
        return count
