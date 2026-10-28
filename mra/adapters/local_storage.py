from __future__ import annotations

from contextlib import suppress
from pathlib import Path


class LocalStorage:
    def __init__(self, root: str | Path = ".local"):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        base = self.root.resolve()
        p = (base / key).resolve()
        if p != base and base not in p.parents:
            raise ValueError(f"key escapes storage root: {key}")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def put_new(self, key: str, data: bytes) -> bool:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(p, "xb") as fh:
                fh.write(data)
            return True
        except FileExistsError:
            return False

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str) -> list[str]:
        base = self.root.resolve()
        if not base.exists():
            return []
        return sorted(k for k in (str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())
                      if k.startswith(prefix))

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list(prefix)
        for k in keys:
            self._path(k).unlink()
        for d in sorted({self._path(k).parent for k in keys}, key=lambda p: -len(p.parts)):
            with suppress(OSError):
                d.rmdir()
        return len(keys)

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size
