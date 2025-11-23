from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_migrations_apply_and_revert(tmp_path, monkeypatch):
    orchestrator_root = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(orchestrator_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(orchestrator_root / "alembic"))

    db_path = tmp_path / "migration.db"
    db_url = f"sqlite:///{db_path}"
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    monkeypatch.setenv("DATABASE_URL", db_url)

    command.upgrade(alembic_cfg, "head")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    assert {"config", "encoding_profiles", "library_entries", "job_history"}.issubset(table_names)

    command.downgrade(alembic_cfg, "base")
    inspector = inspect(engine)
    remaining_tables = set(inspector.get_table_names())
    assert remaining_tables <= {"alembic_version"}
