"""Upgrade/rollback on a new temporary database, never downgrade user data."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings


def main():
    url = make_url(get_settings().database_url)
    name = "stage5_migration_" + uuid4().hex
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
            # Migration diagnostics contain schemas, never print connection strings.
            print(run.stderr[-4000:])
            raise RuntimeError("Isolated migration failed")

    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        migrate("upgrade", "0005_thinking")
        with target.begin() as c:
            c.execute(
                text(
                    "INSERT INTO users(id,email,password_hash,created_at) "
                    "VALUES ('u','stage5@example.test','unused',now())"
                )
            )
            c.execute(
                text(
                    "INSERT INTO courses(id,owner_id,name,created_at) "
                    "VALUES ('c','u','test',now())"
                )
            )
            c.execute(
                text(
                    "INSERT INTO chat_sessions(id,owner_id,course_id,created_at) "
                    "VALUES ('s','u','c',now())"
                )
            )
            c.execute(
                text(
                    "INSERT INTO messages(id,owner_id,session_id,role,content,"
                    "status,thinking,protocol_state,created_at) VALUES "
                    "('m','u','s','assistant','legacy','completed',true,:protocol,now())"
                ),
                {"protocol": json.dumps({"version": 1})},
            )
        migrate("upgrade", "head")
        migrate("check")
        with target.begin() as c:
            row = c.execute(
                text(
                    "SELECT content,thinking,protocol_state,tool_policy "
                    "FROM messages WHERE id='m'"
                )
            ).one()
            assert tuple(row) == ("legacy", True, {"version": 1}, None)
            results.append({"legacy_preserved": True})
        migrate("downgrade", "0005_thinking")
        migrate("upgrade", "head")
        with target.connect() as c:
            assert (
                c.scalar(text("SELECT content FROM messages WHERE id='m'")) == "legacy"
            )
    finally:
        target.dispose()
        with admin.connect() as c:
            c.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:name AND pid<>pg_backend_pid()"
                ),
                {"name": name},
            )
            c.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()
        results.append({"temporary_database_removed": True})
        out = Path("../docs/stage5/checks")
        out.mkdir(parents=True, exist_ok=True)
        file = out / (
            "migration-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json"
        )
        file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("Stage5 isolated migration checks passed; temporary database removed")


if __name__ == "__main__":
    main()
