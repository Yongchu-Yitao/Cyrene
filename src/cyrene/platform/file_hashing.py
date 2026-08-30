"""Content-derived file hashing shared by runtime and persistence layers."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

FileIdentity = tuple[int, int, int, int, int]


def sha256_bytes(content: bytes) -> str:
    """Return the SHA-256 digest for ``content``."""
    return hashlib.sha256(content).hexdigest()


def file_identity(path: str | Path) -> FileIdentity:
    """Return the filesystem identity used to invalidate cached file digests."""
    stat = Path(path).stat()
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_size),
        int(stat.st_mtime_ns),
        int(stat.st_ctime_ns),
    )


@lru_cache(maxsize=512)
def cached_sha256_file(path: str, identity: FileIdentity) -> str:
    """Hash a file once for a specific filesystem identity."""
    del identity
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: str | Path) -> str:
    """Return a file's SHA-256 digest, raising when its bytes are unavailable."""
    file_path = Path(path)
    return cached_sha256_file(str(file_path), file_identity(file_path))


__all__ = [
    "FileIdentity",
    "cached_sha256_file",
    "file_identity",
    "sha256_bytes",
    "sha256_file",
]
