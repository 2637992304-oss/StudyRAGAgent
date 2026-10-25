from uuid import uuid4

from app.providers.local_storage import LocalStorage


def test_local_storage_round_trip(tmp_path):
    storage = LocalStorage(tmp_path / "uploads")
    key = uuid4()
    storage.write(key, b"course notes")
    assert storage.read(key) == b"course notes"
    storage.delete(key)
    assert not (storage.root / str(key)).exists()
