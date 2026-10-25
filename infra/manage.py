"""Maintenance-window backup/empty-target restore. Host Python stdlib only.

Run from any directory; all Compose invocations use this repository explicitly.
Archive contents are private and never suitable for Git or public CI artifacts.
"""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


class Compose:
    def __init__(self, project, env_file):
        self.env = dict(os.environ, APP_ENV_FILE=str(env_file.resolve()))
        self.cmd = [
            "docker",
            "compose",
            "--project-directory",
            str(ROOT),
            "--env-file",
            str(env_file.resolve()),
            "-p",
            project,
            "--profile",
            "app",
            "-f",
            str(ROOT / "docker-compose.yml"),
        ]
        config = json.loads(self.run("config", "--format", "json", capture=True))
        pg = config["services"]["postgres"]["environment"]
        self.user, self.database = pg["POSTGRES_USER"], pg["POSTGRES_DB"]

    def run(self, *args, capture=False, input=None):
        result = subprocess.run(
            self.cmd + list(args),
            cwd=ROOT,
            env=self.env,
            input=input,
            stdout=subprocess.PIPE if capture else None,
            check=True,
        )
        return result.stdout

    def sql(self, query):
        return (
            self.run(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                self.user,
                "-d",
                self.database,
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                query,
                capture=True,
            )
            .decode()
            .strip()
        )

    def payload(self, action, input=None, capture=False):
        return self.run(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "api",
            "python",
            "-m",
            "scripts.backup_payload",
            action,
            input=input,
            capture=capture,
        )


def backup(compose, target, project):
    target.mkdir(parents=True, exist_ok=False)
    # Shut down ingress, wait for SSE/HTTP, then warm-stop the worker. Failure
    # leaves services stopped for investigation; no misleading success manifest.
    compose.run("stop", "frontend")
    compose.run("stop", "api")
    compose.run("stop", "worker")
    compose.payload("quiescent")
    if (
        compose.run(
            "exec", "-T", "redis", "redis-cli", "LLEN", "celery", capture=True
        ).strip()
        != b"0"
    ):
        raise RuntimeError("Queue is not empty; resume and drain before backup")
    dump = compose.run(
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "-U",
        compose.user,
        "-d",
        compose.database,
        "-Fc",
        capture=True,
    )
    (target / "database.dump").write_bytes(dump)
    (target / "storage.tar.gz").write_bytes(compose.payload("pack", capture=True))
    revision = compose.sql("SELECT version_num FROM alembic_version")
    manifest = {
        "format": 1,
        "project": project,
        "schema": revision,
        "created_utc": datetime.now(UTC).isoformat(),
        "files": {
            name: sha(target / name) for name in ("database.dump", "storage.tar.gz")
        },
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print("Consistent backup complete. App remains stopped; see the resume command.")


def restore(compose, source, project):
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest["format"] != 1 or manifest["project"] == project:
        raise ValueError("Restore requires a different, new Compose project")
    if set(manifest["files"]) != {"database.dump", "storage.tar.gz"}:
        raise ValueError("Invalid backup manifest")
    for name, expected in manifest["files"].items():
        if sha(source / name) != expected:
            raise ValueError(f"Backup checksum mismatch: {name}")
    # Do not stop or replace an existing target application.
    ids = compose.run("ps", "-q", "api", "worker", "frontend", capture=True).strip()
    if ids:
        raise ValueError("Target application is running; use a fresh project")
    compose.run("up", "-d", "--wait", "postgres", "redis")
    if (
        compose.sql(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        )
        != "0"
    ):
        raise ValueError("Target database is not empty; restore refused")
    compose.payload("empty")
    if (
        compose.run("exec", "-T", "redis", "redis-cli", "DBSIZE", capture=True).strip()
        != b"0"
    ):
        raise ValueError("Target Redis is not empty; restore refused")
    compose.run(
        "exec",
        "-T",
        "postgres",
        "pg_restore",
        "-U",
        compose.user,
        "-d",
        compose.database,
        "--no-owner",
        "--no-acl",
        "--exit-on-error",
        input=(source / "database.dump").read_bytes(),
    )
    compose.payload("unpack", input=(source / "storage.tar.gz").read_bytes())
    if compose.sql("SELECT version_num FROM alembic_version") != manifest["schema"]:
        raise ValueError("Restored schema does not match backup")
    print(
        "Restored database and storage. Start app and verify data, files and citations."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    compose = Compose(args.project, args.env_file)
    if args.action == "backup":
        backup(compose, args.directory.resolve(), args.project)
    else:
        restore(compose, args.directory.resolve(), args.project)


if __name__ == "__main__":
    main()
