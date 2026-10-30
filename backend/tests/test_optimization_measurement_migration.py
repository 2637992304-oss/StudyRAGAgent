import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_measurement_migration_preserves_old_zero_as_unknown(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "metrics.db").as_posix())
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/0009_message_measurements.py"
    )
    spec = importlib.util.spec_from_file_location("migration0009", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE messages(id TEXT PRIMARY KEY, input_tokens INTEGER)")
        )
        connection.execute(text("INSERT INTO messages VALUES ('old',0)"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            assert connection.execute(
                text("SELECT input_tokens, measurements FROM messages")
            ).one() == (0, None)
            module.downgrade()
        assert "measurements" not in {
            c["name"] for c in inspect(connection).get_columns("messages")
        }
        assert (
            connection.execute(text("SELECT input_tokens FROM messages")).scalar() == 0
        )
    engine.dispose()
