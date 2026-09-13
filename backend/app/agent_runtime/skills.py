from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend.app.agent_runtime.budget import AgentBudget


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: str
    description: str
    instructions_file: str = "SKILL.md"
    preconditions: list[str]
    workflow: list[str]
    allowed_tools: frozenset[str]
    postconditions: list[str]
    stop_rules: list[str]
    resources: list[str] = Field(default_factory=list)
    budget: AgentBudget


class SkillNotFoundError(LookupError):
    pass


class SkillRegistry:
    """加载 JSON-compatible YAML Skill 配置并验证对应指令文件。"""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)
        self._skills: dict[str, SkillDefinition] = {}

    def load(self) -> None:
        loaded: dict[str, SkillDefinition] = {}
        if not self.root.is_dir():
            self._skills = loaded
            return
        for config_path in sorted(self.root.glob("*/skill.yaml")):
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            skill = SkillDefinition.model_validate(raw)
            skill_root = config_path.parent.resolve(strict=True)
            if skill_root.parent != self.root:
                raise ValueError(f"skill path escaped registry root: {config_path}")
            instructions = (skill_root / skill.instructions_file).resolve(strict=True)
            if instructions.parent != skill_root or not instructions.is_file():
                raise ValueError(f"invalid instructions_file for skill {skill.name}")
            if skill.name in loaded:
                raise ValueError(f"duplicate skill {skill.name}")
            loaded[skill.name] = skill
        self._skills = loaded

    def get(self, name: str) -> SkillDefinition:
        try:
            return self._skills[name]
        except KeyError as error:
            raise SkillNotFoundError(f"skill {name} is not registered") from error

    def names(self) -> list[str]:
        return sorted(self._skills)
