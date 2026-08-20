from __future__ import annotations

from pathlib import Path


class UnsafePathError(PermissionError):
    pass


class WorkspacePathPolicy:
    _blocked_parts = frozenset({".git", ".venv", "node_modules", "__pycache__"})
    _blocked_names = frozenset({".env", "id_rsa", "id_ed25519"})
    _blocked_suffixes = frozenset({".key", ".pem"})

    def __init__(self, workspace_root: str) -> None:
        self.root = Path(workspace_root).resolve(strict=False)

    def resolve(self, relative_path: str) -> Path:
        candidate_path = Path(relative_path)
        if candidate_path.is_absolute():
            raise UnsafePathError("tool paths must be relative to the project workspace")
        if not relative_path.strip() or "\x00" in relative_path:
            raise UnsafePathError("tool path is empty or invalid")

        if self.is_protected(candidate_path):
            raise UnsafePathError("access to secret-bearing files is denied")

        resolved = (self.root / candidate_path).resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as error:
            raise UnsafePathError("path escapes the project workspace") from error
        return resolved

    @classmethod
    def is_protected(cls, relative_path: Path) -> bool:
        lowered_parts = {part.lower() for part in relative_path.parts}
        name = relative_path.name.lower()
        return bool(
            lowered_parts & cls._blocked_parts
            or name in cls._blocked_names
            or name.startswith(".env.")
            or relative_path.suffix.lower() in cls._blocked_suffixes
        )
