from __future__ import annotations

import hashlib

from backend.app.domain.rag import SourceFile
from backend.app.tools.path_policy import WorkspacePathPolicy


MAX_SOURCE_BYTES = 1_000_000
LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sql": "sql",
}
INDEX_BLOCKED_PARTS = frozenset(
    {
        ".devteam",
        "data",
        "dist",
        "build",
        "coverage",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)


class RepositoryScanner:
    def scan(self, workspace_root: str) -> list[SourceFile]:
        policy = WorkspacePathPolicy(workspace_root)
        root = policy.root
        if not root.is_dir():
            raise FileNotFoundError("project workspace does not exist")
        files: list[SourceFile] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            lowered_parts = {part.lower() for part in relative.parts}
            if policy.is_protected(relative) or lowered_parts & INDEX_BLOCKED_PARTS:
                continue
            language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
            if language is None or path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            try:
                raw = path.read_bytes()
                content = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            files.append(
                SourceFile(
                    path=relative.as_posix(),
                    language=language,
                    content=content,
                    content_hash=hashlib.sha256(raw).hexdigest(),
                )
            )
        return files
