import pytest
from pydantic import ValidationError

from backend.app.domain.artifacts import ComponentDesign, PRDArtifact, ReviewArtifact


def test_prd_rejects_unknown_requirement_link() -> None:
    with pytest.raises(ValidationError):
        PRDArtifact.model_validate(
            {
                "title": "Example",
                "background": "Background",
                "problem_statement": "Problem",
                "goals": ["Goal"],
                "user_stories": [
                    {"id": "US-001", "role": "user", "goal": "use it", "benefit": "value"}
                ],
                "requirements": [
                    {"id": "FR-001", "description": "Feature", "priority": "MUST"}
                ],
                "acceptance_criteria": [
                    {
                        "id": "AC-001",
                        "requirement_ids": ["FR-999"],
                        "condition": "When used",
                        "expected_result": "It works",
                    }
                ],
            }
        )


def test_approved_review_rejects_blocking_issue() -> None:
    with pytest.raises(ValidationError):
        ReviewArtifact.model_validate(
            {
                "verdict": "APPROVED",
                "summary": "Cannot approve",
                "reviewed_files": ["module.py"],
                "issues": [
                    {
                        "id": "REV-001",
                        "severity": "MAJOR",
                        "category": "correctness",
                        "path": "module.py",
                        "description": "Incorrect behavior",
                        "evidence": "The branch returns the wrong value",
                        "recommendation": "Correct the branch",
                    }
                ],
            }
        )


def test_component_interface_supports_beginner_details_and_old_documents() -> None:
    legacy = ComponentDesign.model_validate(
        {
            "name": "Navigation",
            "responsibility": "Render navigation links",
            "interfaces": ["Props: activeLink"],
        }
    )
    explained = ComponentDesign.model_validate(
        {
            "name": "Navigation",
            "responsibility": "Render navigation links",
            "interfaces": ["Props: activeLink"],
            "interface_details": [
                {
                    "name": "activeLink",
                    "kind": "React Props",
                    "purpose": "标记当前选中的导航项",
                    "inputs": ["当前页面标识"],
                    "outputs": ["选中状态的导航样式"],
                    "usage_example": '<Navigation activeLink="tasks" />',
                    "beginner_explanation": "父组件通过这个属性告诉导航组件当前位于哪个页面。",
                }
            ],
        }
    )

    assert legacy.interface_details == []
    assert explained.interface_details[0].name == "activeLink"
