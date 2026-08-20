import pytest
from pydantic import ValidationError

from backend.app.domain.artifacts import PRDArtifact, ReviewArtifact


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
