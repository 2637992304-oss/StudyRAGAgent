from pathlib import Path
from uuid import UUID


class LocalStorage:
    """Store opaque object keys beneath a local root directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: UUID) -> Path:
        return self.root / str(key)

    def write(self, key: UUID, content: bytes) -> None:
        self._path(key).write_bytes(content)

    def read(self, key: UUID) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: UUID) -> None:
        self._path(key).unlink(missing_ok=True)
