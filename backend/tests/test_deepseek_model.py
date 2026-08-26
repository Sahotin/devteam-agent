from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError
from pydantic import BaseModel

from backend.app.core.config import Settings
from backend.app.domain.artifacts import DeveloperPlan
from backend.app.infrastructure.llm.deepseek_model import (
    DeepSeekStructuredModel,
    DeepSeekStructuredModelResponseError,
)
from backend.app.infrastructure.llm.errors import ModelConnectionError, ModelTimeoutError
from backend.app.infrastructure.llm.telemetry import capture_model_usage


class ExampleOutput(BaseModel):
    title: str
    count: int


def test_developer_plan_schema_distinguishes_create_and_replace_requirements() -> None:
    schema = DeveloperPlan.model_json_schema()
    mutations = schema["properties"]["mutations"]["items"]

    assert mutations["discriminator"]["propertyName"] == "operation"
    replace_ref = next(
        item["$ref"]
        for item in mutations["oneOf"]
        if item["$ref"].endswith("/ReplaceFileMutation")
    )
    replace_schema = schema["$defs"][replace_ref.rsplit("/", 1)[-1]]
    assert {"old_text", "new_text", "expected_sha256"}.issubset(
        replace_schema["required"]
    )


class FakeCompletions:
    def __init__(self, contents: list[str | None | Exception], usage=None) -> None:
        self.contents = contents
        self.usage = usage
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.contents.pop(0)
        if isinstance(content, Exception):
            raise content
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=self.usage,
        )


class FakeClient:
    def __init__(self, contents: list[str | None | Exception], usage=None) -> None:
        self.completions = FakeCompletions(contents, usage=usage)
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.mark.asyncio
async def test_deepseek_model_requests_json_and_validates_schema() -> None:
    client = FakeClient(['{"title":"完成","count":2}'])
    model = DeepSeekStructuredModel(
        api_key="not-a-real-key",
        model="deepseek-v4-pro",
        client=client,
    )

    result = await model.generate(
        system_prompt="生成结果",
        payload={"requirement": "健康检查"},
        output_schema=ExampleOutput,
    )

    assert result == ExampleOutput(title="完成", count=2)
    call = client.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["model"] == "deepseek-v4-pro"
    assert call["max_tokens"] == 16384
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "JSON Schema" in call["messages"][0]["content"]
    assert json.loads(call["messages"][1]["content"]) == {
        "requirement": "健康检查"
    }


@pytest.mark.asyncio
async def test_deepseek_model_reports_provider_token_usage() -> None:
    usage = SimpleNamespace(
        prompt_tokens=120,
        completion_tokens=30,
        total_tokens=150,
        prompt_cache_hit_tokens=20,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=8),
    )
    model = DeepSeekStructuredModel(
        api_key="not-a-real-key",
        model="deepseek-v4-pro",
        client=FakeClient(['{"title":"完成","count":2}'], usage=usage),
    )
    captured = []

    with capture_model_usage(captured.append):
        await model.generate(
            system_prompt="生成结果",
            payload={},
            output_schema=ExampleOutput,
        )

    assert len(captured) == 1
    assert captured[0].input_tokens == 120
    assert captured[0].output_tokens == 30
    assert captured[0].total_tokens == 150
    assert captured[0].cached_input_tokens == 20
    assert captured[0].reasoning_tokens == 8


@pytest.mark.asyncio
async def test_deepseek_model_retries_invalid_structured_output() -> None:
    client = FakeClient(['{"title":"incomplete"}', '{"title":"完成","count":2}'])
    model = DeepSeekStructuredModel(
        api_key="not-a-real-key",
        model="deepseek-v4-pro",
        max_retries=1,
        client=client,
    )

    result = await model.generate(
        system_prompt="生成结果",
        payload={},
        output_schema=ExampleOutput,
    )

    assert result.count == 2
    assert len(client.completions.calls) == 2
    assert "Schema" in client.completions.calls[-1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_deepseek_model_rejects_persistently_invalid_output() -> None:
    model = DeepSeekStructuredModel(
        api_key="not-a-real-key",
        model="deepseek-v4-pro",
        max_retries=0,
        client=FakeClient(
            [
                '{"unexpected":true}',
                '{"still":"invalid"}',
                '{"still":"invalid-again"}',
            ]
        ),
    )
    with pytest.raises(DeepSeekStructuredModelResponseError):
        await model.generate(
            system_prompt="system",
            payload={},
            output_schema=ExampleOutput,
        )


@pytest.mark.asyncio
async def test_deepseek_model_corrects_invalid_schema_even_without_network_retries() -> None:
    client = FakeClient(['{"title":"incomplete"}', '{"title":"完成","count":2}'])
    model = DeepSeekStructuredModel(
        api_key="not-a-real-key",
        model="deepseek-v4-pro",
        max_retries=0,
        client=client,
    )

    result = await model.generate(
        system_prompt="生成结果",
        payload={},
        output_schema=ExampleOutput,
    )

    assert result == ExampleOutput(title="完成", count=2)
    assert len(client.completions.calls) == 2


@pytest.mark.asyncio
async def test_deepseek_model_allows_two_targeted_schema_corrections() -> None:
    client = FakeClient(
        [
            '{"title":"missing-count"}',
            '{"count":2}',
            '{"title":"完成","count":2}',
        ]
    )
    model = DeepSeekStructuredModel(
        api_key="not-a-real-key",
        model="deepseek-v4-pro",
        max_retries=0,
        client=client,
    )

    result = await model.generate(
        system_prompt="生成结果",
        payload={},
        output_schema=ExampleOutput,
    )

    assert result == ExampleOutput(title="完成", count=2)
    assert len(client.completions.calls) == 3


@pytest.mark.asyncio
async def test_deepseek_model_accepts_json_inside_markdown_fence() -> None:
    model = DeepSeekStructuredModel(
        api_key="not-a-real-key",
        model="deepseek-v4-pro",
        client=FakeClient(['```json\n{"title":"完成","count":2}\n```']),
    )

    result = await model.generate(
        system_prompt="生成结果",
        payload={},
        output_schema=ExampleOutput,
    )

    assert result.count == 2


@pytest.mark.asyncio
async def test_deepseek_model_translates_connection_error_without_leaking_key() -> None:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    connection_error = APIConnectionError(request=request)
    model = DeepSeekStructuredModel(
        api_key="sensitive-value",
        model="deepseek-v4-flash",
        max_retries=2,
        client=FakeClient(
            [connection_error, connection_error, connection_error]
        ),
    )

    with pytest.raises(ModelConnectionError) as captured:
        await model.generate(
            system_prompt="生成结果",
            payload={},
            output_schema=ExampleOutput,
        )

    message = str(captured.value)
    assert "MODEL_CONNECTION_ERROR" in message
    assert "已自动尝试 3 次" in message
    assert "api.deepseek.com" in message
    assert "sensitive-value" not in message


@pytest.mark.asyncio
async def test_deepseek_model_translates_generation_timeout() -> None:
    model = DeepSeekStructuredModel(
        api_key="not-a-real-key",
        model="deepseek-v4-flash",
        timeout_seconds=30,
        max_retries=0,
        client=FakeClient([TimeoutError("generation timed out")]),
    )

    with pytest.raises(ModelTimeoutError) as captured:
        await model.generate(
            system_prompt="生成结果",
            payload={},
            output_schema=ExampleOutput,
        )

    message = str(captured.value)
    assert "MODEL_TIMEOUT_ERROR" in message
    assert "30 秒" in message
    assert "完整生成" in message


def test_deepseek_settings_require_key_and_hide_secret() -> None:
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        Settings(llm_provider="deepseek")

    settings = Settings(
        llm_provider="deepseek",
        llm_model="deepseek-v4-pro",
        deepseek_api_key="sensitive-value",
        deepseek_proxy_url="http://proxy-user:proxy-password@127.0.0.1:7897",
    )
    assert "sensitive-value" not in repr(settings)
    assert "proxy-password" not in repr(settings)
