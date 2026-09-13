from __future__ import annotations

from pydantic import BaseModel, Field


class VerificationEvidence(BaseModel):
    expected_files: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    diff_exists: bool = False
    tests_required: bool = True
    tests_executed: bool = False
    tests_passed: bool = False
    unexpected_files: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    passed: bool
    reasons: list[str]


class AgentVerifier:
    def verify(self, evidence: VerificationEvidence, *, max_changed_files: int) -> VerificationResult:
        reasons: list[str] = []
        if not evidence.changed_files:
            reasons.append("没有检测到代码变更")
        if not evidence.diff_exists:
            reasons.append("没有可验证的代码差异")
        missing = set(evidence.expected_files) - set(evidence.changed_files)
        if missing:
            reasons.append("目标文件未修改：" + "、".join(sorted(missing)))
        if evidence.unexpected_files:
            reasons.append("出现未允许文件：" + "、".join(evidence.unexpected_files))
        if len(set(evidence.changed_files)) > max_changed_files:
            reasons.append("修改文件数超过预算")
        if evidence.tests_required and not evidence.tests_executed:
            reasons.append("尚未执行测试")
        elif evidence.tests_required and not evidence.tests_passed:
            reasons.append("测试未通过")
        return VerificationResult(passed=not reasons, reasons=reasons)
