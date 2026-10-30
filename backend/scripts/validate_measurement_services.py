"""Disposable O07/O09 PostgreSQL+Redis regression. Explicit isolated DB targets.

From backend: python -m scripts.validate_measurement_services
Docker must already be running. Uses fresh names, labels and dynamic local ports.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4


def command(args, **kwargs):
    return subprocess.run(
        args, check=True, capture_output=True, text=True, **kwargs
    ).stdout.strip()


def run(*, extra_checks=None, output_group="optimization34", skip_full=False):
    command(["docker", "info", "--format", "{{.ServerVersion}}"])
    identity = uuid4().hex
    prefix = "studyrag-opt34-" + identity[:12]
    root = Path(__file__).resolve().parents[2]
    if output_group not in {"optimization34", "optimization56"}:
        raise ValueError("invalid_validation_group")
    evidence = root / ".runtime" / output_group
    evidence.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="services-", dir=evidence))
    created = []

    def create(name, image, port, variables):
        args = [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label",
            "studyrag.opt34=" + identity,
            "-p",
            f"127.0.0.1::{port}",
        ]
        for key, value in variables.items():
            args += ["-e", key + "=" + value]
        command(args + [image])
        created.append(name)
        binding = command(["docker", "port", name, str(port)])
        return binding.rsplit(":", 1)[1]

    try:
        pg = create(
            prefix + "-pg",
            "pgvector/pgvector:pg16",
            5432,
            {
                "POSTGRES_USER": "opt34",
                "POSTGRES_PASSWORD": "disposable-only",
                "POSTGRES_DB": "validation",
            },
        )
        redis = create(prefix + "-redis", "redis:7-alpine", 6379, {})
        for _ in range(60):
            try:
                command(
                    [
                        "docker",
                        "exec",
                        prefix + "-pg",
                        "pg_isready",
                        "-U",
                        "opt34",
                        "-d",
                        "validation",
                    ]
                )
                command(["docker", "exec", prefix + "-redis", "redis-cli", "ping"])
                break
            except subprocess.CalledProcessError:
                time.sleep(0.5)
        else:
            raise RuntimeError("isolated_services_not_ready")
        (output / "storage-full").mkdir()
        environment = dict(
            os.environ,
            PYTHONIOENCODING="utf-8",
            DATABASE_URL=f"postgresql+psycopg://opt34:disposable-only@127.0.0.1:{pg}/validation",
            REDIS_URL=f"redis://127.0.0.1:{redis}/0",
            AUTH_SECRET="opt34-isolated-only",
            LLM_API_KEY="",
            LLM_BASE_URL="",
            LLM_MODEL="",
            TAVILY_API_KEY="",
            GITHUB_CREDENTIALS="{}",
            OPT12_LIVE="0",
            OPT56_LIVE="0",
            STORAGE_DIR=str(output / "storage-full"),
            OPT34_LIVE="1",
            OPT34_RUN_ID=identity,
            OPT34_CONTAINER_PREFIX=prefix,
        )
        backend = root / "backend"
        for args in (
            ["-m", "alembic", "upgrade", "head"],
            ["-m", "alembic", "check"],
            [
                "-m",
                "pytest",
                "tests",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(output / "pytest"),
                "--junitxml",
                str(output / "backend.xml"),
            ],
        ):
            if skip_full and "pytest" in args:
                continue
            result = subprocess.run(
                [sys.executable, *args],
                env=environment,
                cwd=backend,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            # Test details stay local, never upload private environment settings.
            log_name = (
                "pytest.txt"
                if "pytest" in args
                else "upgrade.txt"
                if "upgrade" in args
                else "schema-check.txt"
            )
            (output / log_name).write_text(
                result.stdout + result.stderr, encoding="utf-8"
            )
            if result.returncode:
                raise RuntimeError("isolated_check_failed_see_local_output")
        extra_result = (
            extra_checks(environment, output, prefix, identity)
            if extra_checks is not None
            else None
        )
        (output / "summary.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "full_regression": not skip_full,
                    "scope": "fresh PostgreSQL/pgvector and Redis regression",
                    "external_model_requests": None
                    if extra_result and extra_result.get("real_model_evaluation")
                    else 0,
                    "engineering_external_model_requests": 0,
                    "extra_checks": extra_result,
                }
            ),
            encoding="utf-8",
        )
        print(json.dumps({"passed": True, "output": str(output)}))
    finally:
        for name in reversed(created):
            raw = json.loads(command(["docker", "inspect", name]))[0]
            if raw["Config"]["Labels"].get("studyrag.opt34") != identity:
                raise RuntimeError("container_identity_mismatch")
            command(["docker", "rm", "-f", "-v", name])
        temporary = output / "pytest"
        if temporary.exists():
            import shutil

            if (
                temporary.resolve().parent != output.resolve()
                or output.resolve().parent != evidence.resolve()
            ):
                raise RuntimeError("cleanup_path_outside_run")
            shutil.rmtree(temporary)
        storage = output / "storage-full"
        if storage.exists():
            import shutil

            if storage.resolve().parent != output.resolve():
                raise RuntimeError("storage_cleanup_outside_run")
            shutil.rmtree(storage)


if __name__ == "__main__":
    run()
