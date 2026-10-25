"""Private volume payload for infra/manage.py; never writes an archive to images."""

import io
import sys
import tarfile
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import IngestionJob, Message
from app.db.session import get_engine


def main():
    root = get_settings().storage_dir
    root.mkdir(parents=True, exist_ok=True)
    action = sys.argv[1]
    if action == "quiescent":
        with Session(get_engine()) as db:
            jobs = db.scalar(
                select(func.count())
                .select_from(IngestionJob)
                .where(IngestionJob.status.in_(["queued", "processing"]))
            )
            messages = db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.status == "pending")
            )
        if jobs or messages:
            raise SystemExit(
                "Unfinished jobs/messages: resume services and resolve first"
            )
    elif action == "pack":
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
            for path in sorted(root.iterdir()):
                UUID(path.name)
                if not path.is_file() or path.is_symlink():
                    raise ValueError("Unexpected storage entry")
                archive.add(path, arcname=path.name, recursive=False)
    elif action == "empty":
        if any(root.iterdir()):
            raise SystemExit("Restore requires empty storage")
    elif action == "unpack":
        if any(root.iterdir()):
            raise SystemExit("Restore requires empty storage")
        # Read and validate all members before writing any file.
        with tarfile.open(
            fileobj=io.BytesIO(sys.stdin.buffer.read()), mode="r:gz"
        ) as archive:
            names = set()
            for member in archive.getmembers():
                UUID(member.name)
                if (
                    not member.isfile()
                    or member.name in names
                    or "/" in member.name
                    or "\\" in member.name
                ):
                    raise ValueError("Unsafe archive entry")
                names.add(member.name)
            archive.extractall(root, filter="data")
    else:
        raise SystemExit("Unknown action")


if __name__ == "__main__":
    main()
