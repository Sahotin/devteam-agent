from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from backend.app.domain.rag import SourceFile


MAX_CHUNK_LINES = 120
CHUNK_OVERLAP_LINES = 15
MAX_CHUNK_CHARS = 16_000


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    start_line: int
    end_line: int
    content: str
    symbol_name: str | None = None
    symbol_type: str | None = None


class LanguageAwareChunker:
    def chunk(self, source: SourceFile) -> list[ChunkDraft]:
        if source.language == "python":
            return self._python_chunks(source.content)
        if source.language == "markdown":
            return self._markdown_chunks(source.content)
        return self._window_chunks(source.content, language=source.language)

    def _python_chunks(self, content: str) -> list[ChunkDraft]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._window_chunks(content, language="python")
        lines = content.splitlines()
        nodes = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and hasattr(node, "end_lineno")
        ]
        drafts: list[ChunkDraft] = []
        cursor = 1
        for node in nodes:
            decorator_lines = [item.lineno for item in getattr(node, "decorator_list", [])]
            node_start = min([node.lineno, *decorator_lines])
            if node_start > cursor:
                module_content = self._slice(lines, cursor, node_start - 1)
                if module_content.strip():
                    drafts.extend(
                        self._split_range(
                            lines, cursor, node_start - 1, None, "module"
                        )
                    )
            symbol_type = "class" if isinstance(node, ast.ClassDef) else "function"
            drafts.extend(
                self._split_range(
                    lines,
                    node_start,
                    node.end_lineno or node.lineno,
                    node.name,
                    symbol_type,
                )
            )
            cursor = (node.end_lineno or node.lineno) + 1
        if cursor <= len(lines):
            suffix = self._slice(lines, cursor, len(lines))
            if suffix.strip():
                drafts.extend(
                    self._split_range(lines, cursor, len(lines), None, "module")
                )
        if not drafts:
            return self._window_chunks(content, language="python")
        return drafts

    def _markdown_chunks(self, content: str) -> list[ChunkDraft]:
        lines = content.splitlines()
        headings = [
            (index, match.group(1).strip())
            for index, line in enumerate(lines, start=1)
            if (match := re.match(r"^#{1,6}\s+(.+)$", line))
        ]
        if not headings:
            return self._window_chunks(content, language="markdown")
        drafts: list[ChunkDraft] = []
        if headings[0][0] > 1:
            drafts.extend(self._split_range(lines, 1, headings[0][0] - 1, None, "document"))
        for position, (start, title) in enumerate(headings):
            end = headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines)
            drafts.extend(self._split_range(lines, start, end, title, "section"))
        return drafts

    def _window_chunks(self, content: str, language: str) -> list[ChunkDraft]:
        lines = content.splitlines()
        if not lines:
            return []
        symbol_pattern = re.compile(
            r"\b(?:class|function|def|interface|enum|record)\s+([A-Za-z_$][\w$]*)"
        )
        drafts: list[ChunkDraft] = []
        start = 1
        while start <= len(lines):
            end = min(start + MAX_CHUNK_LINES - 1, len(lines))
            chunk_content = self._slice(lines, start, end)
            match = symbol_pattern.search(chunk_content)
            drafts.append(
                ChunkDraft(
                    start_line=start,
                    end_line=end,
                    content=chunk_content,
                    symbol_name=match.group(1) if match else None,
                    symbol_type="symbol" if match else language,
                )
            )
            if end == len(lines):
                break
            start = end - CHUNK_OVERLAP_LINES + 2
        return drafts

    def _split_range(
        self,
        lines: list[str],
        start: int,
        end: int,
        symbol_name: str | None,
        symbol_type: str,
    ) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        cursor = start
        part = 1
        while cursor <= end:
            chunk_end = min(cursor + MAX_CHUNK_LINES - 1, end)
            name = symbol_name
            if symbol_name and end - start + 1 > MAX_CHUNK_LINES:
                name = f"{symbol_name}#part-{part}"
            drafts.append(
                ChunkDraft(
                    start_line=cursor,
                    end_line=chunk_end,
                    content=self._slice(lines, cursor, chunk_end),
                    symbol_name=name,
                    symbol_type=symbol_type,
                )
            )
            if chunk_end == end:
                break
            cursor = chunk_end - CHUNK_OVERLAP_LINES + 2
            part += 1
        return drafts

    @staticmethod
    def _slice(lines: list[str], start: int, end: int) -> str:
        return "\n".join(lines[start - 1 : end])[:MAX_CHUNK_CHARS]
