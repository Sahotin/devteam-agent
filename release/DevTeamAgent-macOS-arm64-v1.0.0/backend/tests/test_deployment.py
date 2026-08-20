from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_compose_uses_migrations_healthchecks_and_security_boundaries() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    app = compose["services"]["app"]
    database = compose["services"]["database"]

    assert app["environment"]["DEVTEAM_DATABASE_AUTO_CREATE"] == "false"
    assert app["environment"]["OPENAI_API_KEY"] == "${OPENAI_API_KEY:-}"
    assert app["environment"]["DEEPSEEK_API_KEY"] == "${DEEPSEEK_API_KEY:-}"
    assert app["read_only"] is True
    assert app["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in app["security_opt"]
    assert app["depends_on"]["database"]["condition"] == "service_healthy"
    assert database["healthcheck"]["test"][0] == "CMD-SHELL"


def test_runtime_image_runs_as_non_root_and_migrates_before_start() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER devteam" in dockerfile
    assert "alembic upgrade head && uvicorn" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert dockerfile.index("USER devteam") < dockerfile.index("CMD [")
