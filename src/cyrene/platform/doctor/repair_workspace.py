"""Bounded plugin snapshots and exact, compare-and-swap source changes."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile

EDITABLE_SUFFIXES = {'.py', '.json', '.toml', '.js', '.jsx', '.html', '.css', '.md', '.txt'}
MAX_FILE = 256_000
MAX_TOTAL = 4_000_000


def target_path(root: Path, target: str) -> Path:
    if not target or target.startswith('.') or '/' in target or '\\' in target:
        raise ValueError('Select one top-level plugin')
    path = root.resolve() / target
    if path.is_symlink() or not path.exists():
        raise ValueError('Plugin is missing or is a symbolic link')
    return path


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or '\\' in value or path.is_absolute() or any(p in {'.', '..'} or p.startswith('.') for p in value.split('/')):
        raise ValueError('Invalid plugin file path')
    return path.as_posix()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fingerprint(manifest: dict) -> str:
    return digest(json.dumps(manifest, sort_keys=True).encode())


def snapshot(root: Path, target: str) -> dict[str, bytes]:
    path = target_path(root, target)
    files, total = {}, 0
    entries = path.rglob('*') if path.is_dir() else [path]
    for item in entries:
        relative = item.relative_to(root.resolve()).as_posix()
        if '__pycache__' in Path(relative).parts:
            continue
        if item.is_symlink():
            raise ValueError('Plugin snapshots do not follow symbolic links')
        if item.is_dir():
            continue
        if not item.is_file() or item.stat().st_size > MAX_FILE:
            raise ValueError('Unsupported or oversized plugin file')
        raw = item.read_bytes()
        total += len(raw)
        if len(raw) > MAX_FILE or total > MAX_TOTAL or len(files) >= 200:
            raise ValueError('Plugin exceeds the repair snapshot limit')
        files[relative] = raw
    if not files:
        raise ValueError('Plugin contains no repairable files')
    return files


def manifest(files: dict[str, bytes]) -> dict[str, str]:
    return {name: digest(raw) for name, raw in files.items()}


def materialize(directory: Path, files: dict[str, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        path = directory / relative_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def apply_changes(files: dict[str, bytes], changes: list[dict]) -> dict[str, bytes]:
    if not 1 <= len(changes) <= 8:
        raise ValueError('A repair must change between one and eight source files')
    result, seen = dict(files), set()
    for change in changes:
        name = relative_path(change['path'])
        if name in seen or name not in files or PurePosixPath(name).suffix not in EDITABLE_SUFFIXES:
            raise ValueError('Only distinct existing source files in the selected plugin can be edited')
        seen.add(name)
        before, after = change['before'], change['after']
        if not isinstance(before, str) or not isinstance(after, str) or len(after.encode()) > MAX_FILE:
            raise ValueError('Invalid or oversized replacement')
        if files[name] != before.encode() or before == after:
            raise ValueError('Replacement must exactly match the original and change its contents')
        result[name] = after.encode()
    if sum(map(len, result.values())) > MAX_TOTAL:
        raise ValueError('Repaired plugin exceeds snapshot limit')
    return result


def atomic_write(path: Path, raw: bytes) -> None:
    mode = path.stat().st_mode & 0o777
    fd, temporary = tempfile.mkstemp(prefix='.doctor-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def repair_lock(directory: Path):
    """Cross-process nonblocking lock, shared by Doctor commits and rollbacks."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.commit.lock').open('a+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                stream.write(b'0')
                stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Another Doctor repair is committing') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def encode_snapshot(files: dict[str, bytes]) -> dict[str, str]:
    return {name: base64.b64encode(raw).decode() for name, raw in files.items()}


def decode_snapshot(files: dict[str, str]) -> dict[str, bytes]:
    return {name: base64.b64decode(raw, validate=True) for name, raw in files.items()}
