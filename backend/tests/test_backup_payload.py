import io
import sys
import tarfile
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts import backup_payload


def invoke(monkeypatch, root, action, data=b""):
    monkeypatch.setattr(
        backup_payload, "get_settings", lambda: SimpleNamespace(storage_dir=root)
    )
    monkeypatch.setattr(sys, "argv", ["backup_payload", action])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data)))
    backup_payload.main()


def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        for name, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = 1 if kind == tarfile.REGTYPE else 0
            info.linkname = "../escape" if kind == tarfile.SYMTYPE else ""
            tar.addfile(info, io.BytesIO(b"x") if info.size else None)
    return stream.getvalue()


@pytest.mark.parametrize(
    "entries",
    [
        [("../escape", tarfile.REGTYPE)],
        [(str(uuid4()), tarfile.SYMTYPE)],
        [("a" * 32, tarfile.REGTYPE), ("../escape", tarfile.REGTYPE)],
    ],
)
def test_unsafe_archive_rejected_before_any_write(monkeypatch, tmp_path, entries):
    root = tmp_path / "storage"
    with pytest.raises(ValueError):
        invoke(monkeypatch, root, "unpack", archive(entries))
    assert list(root.iterdir()) == []
    assert not (tmp_path / "escape").exists()


def test_restore_refuses_existing_file_and_preserves_it(monkeypatch, tmp_path):
    original = tmp_path / str(uuid4())
    original.write_bytes(b"existing learning data")
    with pytest.raises(SystemExit, match="empty storage"):
        invoke(
            monkeypatch, tmp_path, "unpack", archive([(str(uuid4()), tarfile.REGTYPE)])
        )
    assert original.read_bytes() == b"existing learning data"


def test_restore_original_uuid_file(monkeypatch, tmp_path):
    key = str(uuid4())
    invoke(monkeypatch, tmp_path, "unpack", archive([(key, tarfile.REGTYPE)]))
    assert (tmp_path / key).read_bytes() == b"x"
