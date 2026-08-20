from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from backend.app.infrastructure.database.session import create_schema


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
        "task_policies",
        "task_evaluation_feedback",
    }.issubset(tables)

    command.downgrade(config, "base")
    remaining = set(inspect(engine).get_table_names())
    assert remaining <= {"alembic_version"}
    engine.dispose()


def test_upgrade_adopts_unversioned_legacy_database(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{database_path}"
    engine = create_engine(database_url)
    create_schema(engine)

    # 模拟第一版应用：业务表已存在，但尚未引入 Alembic 版本记录和任务策略表。
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE task_policies")

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert "task_policies" in inspector.get_table_names()
    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    assert revision == "4a8d7c2e1f90"
    engine.dispose()


def test_upgrade_adopts_current_unversioned_database(tmp_path: Path) -> None:
    database_path = tmp_path / "current-unversioned.db"
    database_url = f"sqlite:///{database_path}"
    engine = create_engine(database_url)
    create_schema(engine)

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    assert revision == "4a8d7c2e1f90"
    engine.dispose()


def test_upgrade_adopts_database_with_empty_version_table(tmp_path: Path) -> None:
    database_path = tmp_path / "empty-version.db"
    database_url = f"sqlite:///{database_path}"
    engine = create_engine(database_url)
    create_schema(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    with engine.connect() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    assert revision == "4a8d7c2e1f90"
    engine.dispose()
