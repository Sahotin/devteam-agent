from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_initial_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database_path}")
    tables = set(inspect(engine).get_table_names())
    assert {
        "projects",
        "tasks",
        "artifacts",
        "events",
        "tool_calls",
        "executions",
        "memories",
        "indexed_files",
        "code_chunks",
    }.issubset(tables)

    command.downgrade(config, "base")
    remaining = set(inspect(engine).get_table_names())
    assert remaining <= {"alembic_version"}
    engine.dispose()
