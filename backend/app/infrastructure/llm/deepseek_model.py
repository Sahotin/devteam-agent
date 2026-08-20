from __future__ import annotations

import asyncio
import json
from typing import Any

from openai import APIError, AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic import ValidationError

from backend.app.infrastructure.llm.base import StructuredOutput
from backend.app.infrastructure.llm.errors import translate_openai_error
from backend.app.infrastructure.llm.telemetry import report_model_usage


class DeepSeekStructuredModelResponseError(RuntimeError):
    """DeepSeek 未返回满足目标 Schema 的 JSON 时抛出。"""


class DeepSeekStructuredModel:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.deepseek.com",
        proxy_url: str | None = None,
        thinking_enabled: bool = False,
        max_output_tokens: int = 16384,
        timeout_seconds: float = 180,
        max_retries: int = 2,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._max_retries = max_retries
        self._base_url = base_url
        self._thinking_enabled = thinking_enabled
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        if client is not None:
            self._client = client
        else:
            client_options: dict[str, Any] = {
                "api_key": api_key,
                "base_url": base_url,
                "timeout": timeout_seconds,
                # 重试由本类统一控制，避免 SDK 重试与结构化重试相乘。
                "max_retries": 0,
            }
            if proxy_url:
                client_options["http_client"] = DefaultAsyncHttpxClient(
                    proxy=proxy_url
                )
            self._client = AsyncOpenAI(**client_options)

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
        network_failures = 0
        structured_failures = 0
        # 网络重试和结构化纠错相互独立：即使用户关闭网络重试，
        # 模型返回了不合规 JSON 时仍允许一次有明确校验反馈的纠正。
        while True:
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self._model,
                        messages=messages,
                        response_format={"type": "json_object"},
                        max_tokens=self._max_output_tokens,
                        extra_body={
                            "thinking": {
                                "type": (
                                    "enabled"
                                    if self._thinking_enabled
                                    else "disabled"
                                )
                            }
                        },
                    ),
                    timeout=self._timeout_seconds,
                )
            except APIError as error:
                if network_failures < self._max_retries:
                    await asyncio.sleep(min(2**network_failures, 4))
                    network_failures += 1
                    continue
                raise translate_openai_error(
                    error,
                    provider_name="DeepSeek",
                    base_url=self._base_url,
                    max_retries=self._max_retries,
                ) from error
            except TimeoutError as error:
                if network_failures < self._max_retries:
                    await asyncio.sleep(min(2**network_failures, 4))
                    network_failures += 1
                    continue
                raise TimeoutError(
                    "MODEL_TIMEOUT_ERROR | DeepSeek 完整生成超过"
                    f" {self._timeout_seconds:g} 秒，已停止等待；"
                    "网络连接不一定异常，可能是本次代码上下文或输出内容较大。"
                ) from error
            content = response.choices[0].message.content if response.choices else None
            report_model_usage(getattr(response, "usage", None))
            if not content:
                last_error = ValueError("DeepSeek response contained no content")
                if structured_failures < 2:
                    structured_failures += 1
                    messages.append(
                        {
                            "role": "user",
                            "content": "上一次响应为空。请重新返回完整且合法的 JSON 对象。",
                        }
                    )
                    continue
                break
            try:
                return output_schema.model_validate(self._load_json_object(content))
            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                last_error = exc
                if structured_failures < 2:
                    structured_failures += 1
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
                    continue
                break

        raise DeepSeekStructuredModelResponseError(
            "DeepSeek response did not match the requested structured output；"
            f"校验详情：{str(last_error)[:500]}"
        ) from last_error

    @staticmethod
    def _load_json_object(content: str) -> Any:
        """兼容模型偶尔附带的 Markdown 围栏或 JSON 前后说明。"""
        candidate = content.strip()
        if candidate.startswith("```"):
            first_newline = candidate.find("\n")
            if first_newline >= 0:
                candidate = candidate[first_newline + 1 :]
            if candidate.endswith("```"):
                candidate = candidate[:-3].rstrip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start < 0 or end <= start:
                raise
            return json.loads(candidate[start : end + 1])
