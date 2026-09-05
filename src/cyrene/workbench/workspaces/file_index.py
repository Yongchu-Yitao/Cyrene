"""Immutable sharded file map with copy-on-write edits and cached text totals."""
from collections.abc import Iterator, Mapping, MutableMapping
from typing import Any

_BUCKETS = 256


class FileIndex(Mapping[str, Any]):
    def __init__(self, files: Mapping[str, Any]) -> None:
        buckets: list[dict[str, Any]] = [{} for _ in range(_BUCKETS)]
        self.text_count = 0
        self.text_bytes = 0
        for path, state in files.items():
            buckets[hash(path) % _BUCKETS][path] = state
            if state.text is not None:
                self.text_count += 1
                self.text_bytes += state.size
        self._buckets = tuple(buckets)
        self._size = len(files)

    def __getitem__(self, path: str) -> Any:
        return self._buckets[hash(path) % _BUCKETS][path]

    def __iter__(self) -> Iterator[str]:
        for bucket in self._buckets:
            yield from bucket

    def __len__(self) -> int:
        return self._size

    def edit(self) -> "FileIndexEdit":
        return FileIndexEdit(self)


class FileIndexEdit(MutableMapping[str, Any]):
    def __init__(self, base: FileIndex) -> None:
        self._buckets = list(base._buckets)
        self._changed: set[int] = set()
        self._size = len(base)
        self.text_count = base.text_count
        self.text_bytes = base.text_bytes

    def _writable(self, path: str) -> dict[str, Any]:
        index = hash(path) % _BUCKETS
        if index not in self._changed:
            self._buckets[index] = dict(self._buckets[index])
            self._changed.add(index)
        return self._buckets[index]

    def __getitem__(self, path: str) -> Any:
        return self._buckets[hash(path) % _BUCKETS][path]

    def __iter__(self) -> Iterator[str]:
        for bucket in self._buckets:
            yield from bucket

    def __len__(self) -> int:
        return self._size

    def __setitem__(self, path: str, state: Any) -> None:
        if path in self:
            del self[path]
        self._writable(path)[path] = state
        self._size += 1
        if state.text is not None:
            self.text_count += 1
            self.text_bytes += state.size

    def __delitem__(self, path: str) -> None:
        state = self._writable(path).pop(path)
        self._size -= 1
        if state.text is not None:
            self.text_count -= 1
            self.text_bytes -= state.size

    def clear(self) -> None:
        self._buckets = [{} for _ in range(_BUCKETS)]
        self._changed = set(range(_BUCKETS))
        self._size = self.text_count = self.text_bytes = 0

    def freeze(self) -> FileIndex:
        result = FileIndex.__new__(FileIndex)
        result._buckets = tuple(self._buckets)
        result._size = self._size
        result.text_count = self.text_count
        result.text_bytes = self.text_bytes
        # Future edits must copy again even if the builder is reused.
        self._changed.clear()
        return result
