import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db import models  # noqa: F401
from app.db.base import Base


def test_new_migration_upgrade_defaults_and_downgrade(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "migration.db").as_posix())
    Base.metadata.create_all(engine)
    columns = {
        "documents": ["parse_warnings", "parse_quality", "parser_version"],
        "ingestion_jobs": [
            "execution_token",
            "lease_until",
            "attempts",
            "delivery_attempts",
            "dispatched_at",
        ],
    }
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/0008_ingestion_recovery.py"
    )
    spec = importlib.util.spec_from_file_location("migration0008", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        for table, names in columns.items():
            for name in names:
                connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {name}"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        for table, names in columns.items():
            actual = {c["name"]: c for c in inspect(connection).get_columns(table)}
            assert set(names) <= actual.keys()
            assert set(actual) == set(Base.metadata.tables[table].columns.keys())
        actual = {
            c["name"]: c for c in inspect(connection).get_columns("ingestion_jobs")
        }
        assert actual["attempts"]["default"] in {"'0'", "0"}
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        for table, names in columns.items():
            assert not set(names) & {
                c["name"] for c in inspect(connection).get_columns(table)
            }
    engine.dispose()
