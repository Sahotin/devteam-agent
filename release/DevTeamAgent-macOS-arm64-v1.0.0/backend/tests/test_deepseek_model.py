from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from backend.app.core.config import Settings
from backend.app.infrastructure.llm.deepseek_model import (
    DeepSeekStructuredModel,
    DeepSeekStructuredModelResponseError,
)


class ExampleOutput(BaseModel):
    title: str
    count: int


class FakeCompletions:
    def __init__(self, contents: list[str | None]) -> None:
        self.contents = contents
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.contents.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class FakeClient:
    def __init__(self, contents: list[str | None]) -> None:
        self.completions = FakeCompletions(contents)
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
    assert "JSON Schema" in call["messages"][0]["content"]
    assert json.loads(call["messages"][1]["content"]) == {
        "requirement": "健康检查"
    }


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
        client=FakeClient(['{"unexpected":true}']),
    )
    with pytest.raises(DeepSeekStructuredModelResponseError):
        await model.generate(
            system_prompt="system",
            payload={},
            output_schema=ExampleOutput,
        )


def test_deepseek_settings_require_key_and_hide_secret() -> None:
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        Settings(llm_provider="deepseek")

    settings = Settings(
        llm_provider="deepseek",
        llm_model="deepseek-v4-pro",
        deepseek_api_key="sensitive-value",
    )
    assert "sensitive-value" not in repr(settings)
