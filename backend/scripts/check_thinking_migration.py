"""Migration compatibility on a fresh disposable DB; never downgrade user DB."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings


def main():
    url = make_url(get_settings().database_url)
    name = "thinking_migration_" + uuid4().hex
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    target = create_engine(url.set(database=name))
    results = []
    env = {
        **os.environ,
        "DATABASE_URL": url.set(database=name).render_as_string(hide_password=False),
    }

    def migrate(*args):
        run = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        results.append(
            {"command": "alembic " + " ".join(args), "exit_code": run.returncode}
        )
        if run.returncode:
            raise RuntimeError("Isolated migration failed")

    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        migrate("upgrade", "0004_stage4")
        # A genuine pre-addendum row survives NULL fields and downgrade/upgrade.
        with target.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users(id,email,password_hash,created_at) "
                    "VALUES ('u','isolated@example.test','unused',now())"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO courses(id,owner_id,name,created_at) "
                    "VALUES ('c','u','isolated',now())"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO chat_sessions(id,owner_id,course_id,created_at) "
                    "VALUES ('s','u','c',now())"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO messages(id,owner_id,session_id,role,content,"
                    "status,created_at) VALUES "
                    "('m','u','s','assistant','legacy','completed',now())"
                )
            )
        migrate("upgrade", "head")
        with target.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT content,thinking,call_config,protocol_state,reply_to_id "
                    "FROM messages WHERE id='m'"
                )
            ).one()
            assert tuple(row) == ("legacy", None, None, None, None)
        results.append({"legacy_row_preserved_with_null_fields": True})
        migrate("check")
        migrate("downgrade", "0004_stage4")
        migrate("upgrade", "head")
        with target.connect() as conn:
            assert (
                conn.scalar(text("SELECT content FROM messages WHERE id='m'"))
                == "legacy"
            )
    finally:
        target.dispose()
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:name AND pid<>pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()
        results.append({"isolated_database_removed": True})
        out = Path("../docs/stage4_thinking/migration-checks.json")
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("Isolated migration and legacy compatibility passed; disposable DB removed")


if __name__ == "__main__":
    main()
