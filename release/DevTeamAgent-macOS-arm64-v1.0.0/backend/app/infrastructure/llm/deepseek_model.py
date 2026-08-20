from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI
from pydantic import ValidationError

from backend.app.infrastructure.llm.base import StructuredOutput


class DeepSeekStructuredModelResponseError(RuntimeError):
    """DeepSeek 未返回满足目标 Schema 的 JSON 时抛出。"""


class DeepSeekStructuredModel:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 120,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._max_retries = max_retries
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def generate(
        self,
        *,
        system_prompt: str,
        payload: dict,
        output_schema: type[StructuredOutput],
    ) -> StructuredOutput:
        schema_json = json.dumps(
            output_schema.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        messages = [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n\n"
                    "你必须只返回一个合法的 JSON 对象，不要返回 Markdown 或解释。"
                    f"JSON 必须满足以下 JSON Schema：{schema_json}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content if response.choices else None
            if not content:
                last_error = ValueError("DeepSeek response contained no content")
                if attempt < self._max_retries:
                    messages.append(
                        {
                            "role": "user",
                            "content": "上一次响应为空。请重新返回完整且合法的 JSON 对象。",
                        }
                    )
                continue
            try:
                return output_schema.model_validate(json.loads(content))
            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    messages.extend(
                        [
                            {"role": "assistant", "content": content},
                            {
                                "role": "user",
                                "content": (
                                    "上一次 JSON 未通过 Schema 校验。请根据错误修正后，"
                                    "重新返回完整 JSON，不要解释。校验错误："
                                    f"{str(exc)[:2000]}"
                                ),
                            },
                        ]
                    )

        raise DeepSeekStructuredModelResponseError(
            "DeepSeek response did not match the requested structured output"
        ) from last_error
