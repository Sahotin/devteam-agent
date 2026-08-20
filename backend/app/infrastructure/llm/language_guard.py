from __future__ import annotations

import re
from typing import TypeVar

from pydantic import BaseModel

from backend.app.infrastructure.llm.base import StructuredModel


StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)

CHINESE_OUTPUT_REQUIREMENT = """
所有面向用户阅读的内容必须使用简体中文，包括标题、摘要、原因、说明、建议、
验证说明、限制说明、风险、问题描述和证据。不得输出完整的英文说明句。
代码内容、JSON 字段名、命令、文件路径、代码标识符、协议名、框架名和标准技术名可以保留原文；
保留技术名时，其用途和解释仍必须使用中文。
""".strip()

_CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z]{2,}")
_REFERENCE_PATTERN = re.compile(r"^(?:FR|NFR|AC|US|DEV|ADR|REV)-\d+$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$.-]*$")

_SKIP_SUBTREE_FIELDS = {
    "content",
    "old_text",
    "new_text",
    "tokens",
}
_SKIP_VALUE_FIELDS = {
    "id",
    "path",
    "file",
    "filename",
    "command",
    "runner",
    "status",
    "verdict",
    "template_id",
    "selected_option_id",
    "selection_mode",
    "requirement_ids",
    "acceptance_criteria_ids",
    "expected_sha256",
    "before_sha256",
    "after_sha256",
    "tool_call_id",
    "memory_ids",
    "reviewed_files",
    "expected_files",
    "searched_context",
    "screenshots",
    "stdout_excerpt",
    "stderr_excerpt",
    "technology_stack",
    "interfaces",
}
_TECHNICAL_TERMS = {
    "react",
    "fastapi",
    "sqlite",
    "postgresql",
    "typescript",
    "javascript",
    "node.js",
    "python",
    "html",
    "css",
    "rest api",
    "websocket",
    "bm25",
    "rag",
    "jwt",
    "svg",
    "docker",
    "vite",
    "npm",
    "pytest",
    "pydantic",
    "sqlalchemy",
    "chroma",
    "faiss",
}


def _looks_like_code_or_technical_value(value: str) -> bool:
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered in _TECHNICAL_TERMS:
        return True
    if _REFERENCE_PATTERN.fullmatch(stripped) or _IDENTIFIER_PATTERN.fullmatch(stripped):
        return True
    if "://" in stripped or "\\" in stripped or "/" in stripped:
        return True
    if any(marker in stripped for marker in ("`", "{", "}", "=>", "--", "::")):
        return True
    return False


def find_english_narrative(value: BaseModel | dict | list | str) -> list[str]:
    violations: list[str] = []

    def visit(current: object, path: tuple[str, ...] = ()) -> None:
        if len(violations) >= 8:
            return
        if isinstance(current, BaseModel):
            visit(current.model_dump(mode="json"), path)
            return
        if isinstance(current, dict):
            for key, item in current.items():
                key_text = str(key)
                if key_text in _SKIP_SUBTREE_FIELDS:
                    continue
                visit(item, (*path, key_text))
            return
        if isinstance(current, list):
            for index, item in enumerate(current):
                visit(item, (*path, str(index)))
            return
        if not isinstance(current, str) or not current.strip():
            return
        field_name = next((part for part in reversed(path) if not part.isdigit()), "")
        if field_name in _SKIP_VALUE_FIELDS:
            return
        if _CHINESE_PATTERN.search(current) or _looks_like_code_or_technical_value(current):
            return
        english_words = _ENGLISH_WORD_PATTERN.findall(current)
        if english_words:
            violations.append(f"{'.'.join(path) or 'root'}: {current[:180]}")

    visit(value)
    return violations


class ChineseOutputGuardModel:
    """发现英文叙述时要求模型重新输出一次，代码与标准技术名不受影响。"""

    def __init__(self, model: StructuredModel) -> None:
        self._model = model

    async def generate(
        self,
        *,
        system_prompt: str,
        payload: dict,
        output_schema: type[StructuredOutput],
    ) -> StructuredOutput:
        guarded_prompt = f"{system_prompt}\n\n{CHINESE_OUTPUT_REQUIREMENT}"
        result = await self._model.generate(
            system_prompt=guarded_prompt,
            payload=payload,
            output_schema=output_schema,
        )
        violations = find_english_narrative(result)
        if not violations:
            return result
        return await self._model.generate(
            system_prompt=guarded_prompt,
            payload={
                **payload,
                "language_correction": {
                    "instruction": (
                        "上一份输出包含英文说明句。保持事实、编号、代码和结构不变，"
                        "仅把面向用户的叙述改为简体中文。"
                    ),
                    "fields": violations,
                },
            },
            output_schema=output_schema,
        )

