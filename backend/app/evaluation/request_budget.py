"""Persistent per-task HTTP request reservations, shared across processes."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from uuid import uuid4


class RequestBudget:
    def __init__(self, path, limit=60):
        self.path = Path(path)
        self.limit = limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        with self._locked():
            if self.path.exists():
                data = self._read()
                if data["limit"] != limit:
                    raise ValueError("request_budget_limit_mismatch")
            else:
                self._write({"limit": limit, "reserved": 0})

    @contextmanager
    def _locked(self):
        with self.lock:
            with self.path.with_suffix(".lock").open("a+b") as stream:
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _read(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if (
            type(data.get("reserved")) is not int
            or not 0 <= data["reserved"] <= self.limit
        ):
            raise ValueError("request_budget_corrupt")
        return data

    def _write(self, data):
        temporary = self.path.with_name(self.path.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @property
    def used(self):
        with self._locked():
            return self._read()["reserved"]

    def reserve(self):
        with self._locked():
            data = self._read()
            if data["reserved"] >= self.limit:
                raise RuntimeError("external_request_budget_exhausted")
            data["reserved"] += 1
            self._write(data)
