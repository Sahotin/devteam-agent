from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend.app.tools.base import BaseTool, ToolContext
from backend.app.tools.path_policy import WorkspacePathPolicy


class CodeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=300)
    file_pattern: str = Field(default="*", max_length=100)
    regex: bool = False
    case_sensitive: bool = False
    max_results: int = Field(default=20, ge=1, le=100)


class CodeMatch(BaseModel):
    path: str
    line: int
    snippet: str


class CodeSearchOutput(BaseModel):
    matches: list[CodeMatch]
    scanned_files: int
    truncated: bool


class CodeSearchTool(BaseTool):
    name = "code.search"
    description = "在项目工作区内搜索文本或正则表达式"
    required_permission = "code:search"
    input_model = CodeSearchInput
    output_model = CodeSearchOutput

    async def execute(
        self, context: ToolContext, input_data: CodeSearchInput
    ) -> CodeSearchOutput:
        policy = WorkspacePathPolicy(context.workspace_root)
        root = policy.root
        if not root.is_dir():
            raise FileNotFoundError("project workspace does not exist")

        flags = 0 if input_data.case_sensitive else re.IGNORECASE
        pattern = (
            re.compile(input_data.query, flags)
            if input_data.regex
            else re.compile(re.escape(input_data.query), flags)
        )
        matches: list[CodeMatch] = []
        scanned_files = 0
        truncated = False

        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if policy.is_protected(relative):
                continue
            if not fnmatch.fnmatch(relative.as_posix(), input_data.file_pattern):
                continue
            if path.stat().st_size > 1_000_000:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            scanned_files += 1
            for line_number, line in enumerate(content.splitlines(), start=1):
                if pattern.search(line):
                    matches.append(
                        CodeMatch(
                            path=relative.as_posix(),
                            line=line_number,
                            snippet=line.strip()[:500],
                        )
                    )
                    if len(matches) >= input_data.max_results:
                        truncated = True
                        return CodeSearchOutput(
                            matches=matches,
                            scanned_files=scanned_files,
                            truncated=truncated,
                        )
        return CodeSearchOutput(
            matches=matches,
            scanned_files=scanned_files,
            truncated=truncated,
        )

    def audit_output(self, output: CodeSearchOutput) -> dict:
        return {
            "match_count": len(output.matches),
            "scanned_files": output.scanned_files,
            "truncated": output.truncated,
            "snippets_redacted": True,
        }
