from pydantic import BaseModel

from backend.app.infrastructure.llm.language_guard import (
    ChineseOutputGuardModel,
    find_english_narrative,
)


class ExampleArtifact(BaseModel):
    summary: str
    verification_notes: list[str]
    path: str
    content: str


class CorrectingModel:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate(self, *, system_prompt: str, payload: dict, output_schema):
        self.calls.append({"system_prompt": system_prompt, "payload": payload})
        if len(self.calls) == 1:
            return output_schema(
                summary="Implementation completed successfully.",
                verification_notes=["All files were checked and are consistent."],
                path="src/App.tsx",
                content="export function App() { return null; }",
            )
        return output_schema(
            summary="实现已成功完成。",
            verification_notes=["已检查所有文件，相关实现保持一致。"],
            path="src/App.tsx",
            content="export function App() { return null; }",
        )


def test_language_guard_finds_narrative_but_ignores_code_and_paths() -> None:
    artifact = ExampleArtifact(
        summary="Implementation completed successfully.",
        verification_notes=["已执行 npm run build。"],
        path="src/App.tsx",
        content="export function App() { return null; }",
    )

    violations = find_english_narrative(artifact)

    assert len(violations) == 1
    assert violations[0].startswith("summary:")


async def test_language_guard_requests_one_chinese_correction() -> None:
    model = CorrectingModel()
    guard = ChineseOutputGuardModel(model)

    artifact = await guard.generate(
        system_prompt="生成结构化产物。",
        payload={"requirement": "示例"},
        output_schema=ExampleArtifact,
    )

    assert artifact.summary == "实现已成功完成。"
    assert len(model.calls) == 2
    assert "language_correction" in model.calls[1]["payload"]
    assert "面向用户阅读的内容必须使用简体中文" in model.calls[0]["system_prompt"]
