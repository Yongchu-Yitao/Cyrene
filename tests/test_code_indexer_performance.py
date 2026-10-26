"""Index scans preserve scope, file bytes and bounded unchanged-file reads."""
import hashlib
import os
from pathlib import Path
import sqlite3

import pytest

from cyrene.plugins.builtin.cyrene_code import indexer


def rows(db):
    with sqlite3.connect(db) as conn:
        return dict(conn.execute('SELECT path, hash FROM files'))


@pytest.mark.parametrize('force', [False, True])
@pytest.mark.parametrize('scope', ['a.py', 'pkg'])
def test_partial_index_preserves_outside_scope(tmp_path, force, scope):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'pkg').mkdir()
    for path in ('a.py', 'b.py', 'pkg/keep.py', 'pkg/gone.py'):
        (project / path).write_text('def example():\n    pass\n')
    db = tmp_path / 'index.sqlite3'
    indexer.build_index(project, db)
    (project / 'pkg/gone.py').unlink()
    indexer.build_index(project, db, path=scope, force=force)
    indexed = rows(db)
    assert {'a.py', 'b.py', 'pkg/keep.py'} <= set(indexed)
    assert ('pkg/gone.py' in indexed) == (scope == 'a.py')


def test_full_scan_removes_deleted_files_and_force_rebuilds(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'a.py').write_text('def a(): pass\n')
    (project / 'b.py').write_text('def b(): pass\n')
    db = tmp_path / 'index.sqlite3'
    indexer.build_index(project, db)
    (project / 'b.py').unlink()
    result = indexer.build_index(project, db, force=True)
    assert set(rows(db)) == {'a.py'}
    assert result['files_indexed'] == 1


def test_same_size_and_restored_mtime_still_updates(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    path = project / 'a.py'
    path.write_text('def old(): pass\n')
    db = tmp_path / 'index.sqlite3'
    indexer.build_index(project, db)
    info = path.stat()
    path.write_text('def new(): pass\n')
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    indexer.build_index(project, db)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT name FROM symbols').fetchall() == [('new',)]
    assert rows(db)['a.py'] == hashlib.md5(path.read_bytes()).hexdigest()


def test_small_file_read_once_and_matches_universal_newlines(tmp_path, monkeypatch):
    path = tmp_path / 'source.py'
    raw = b'def example():\r\n    return 1\r'
    path.write_bytes(raw)
    original = Path.open
    opened = []
    def spy(self, *args, **kwargs):
        if self == path:
            opened.append(args)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', spy)
    digest, source = indexer._read_index_source(path, None)
    assert digest == hashlib.md5(raw).hexdigest()
    assert source == 'def example():\n    return 1\n'
    assert len(opened) == 1


def test_large_unchanged_file_never_uses_unbounded_read(tmp_path, monkeypatch):
    path = tmp_path / 'source.py'
    raw = b'# text\n' * 200000
    path.write_bytes(raw)
    original = Path.open
    class Spy:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.stream.close()
        def fileno(self): return self.stream.fileno()
        def read(self, size=-1):
            assert size == 65536
            return self.stream.read(size)
    def open_file(self, *args, **kwargs):
        stream = original(self, *args, **kwargs)
        return Spy(stream) if self == path else stream
    monkeypatch.setattr(Path, 'open', open_file)
    assert indexer._read_index_source(path, hashlib.md5(raw).hexdigest()) == (hashlib.md5(raw).hexdigest(), None)


def test_changed_large_file_hash_matches_parsed_bytes(tmp_path, monkeypatch):
    path = tmp_path / 'source.py'
    raw = b'def updated(): pass\r\n' + b'# text\r\n' * 200000
    path.write_bytes(raw)
    digest, source = indexer._read_index_source(path, 'old')
    assert digest == hashlib.md5(raw).hexdigest()
    assert source == raw.decode().replace('\r\n', '\n')


def test_excluded_directories_are_pruned_before_traversal(tmp_path, monkeypatch):
    (tmp_path / 'node_modules').mkdir()
    (tmp_path / 'node_modules/ignored.py').write_text('')
    (tmp_path / 'included.py').write_text('')
    visited = []
    original = os.scandir
    def scan(path):
        visited.append(Path(path))
        return original(path)
    monkeypatch.setattr(os, 'scandir', scan)
    assert indexer._collect_py_files(tmp_path) == [tmp_path / 'included.py']
    assert tmp_path / 'node_modules' not in visited


def test_unreadable_or_unstable_file_preserves_old_index(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    path = project / 'a.py'
    path.write_text('def old(): pass\n')
    db = tmp_path / 'index.sqlite3'
    indexer.build_index(project, db)
    before = rows(db)
    original = indexer._file_identity
    count = 0
    def changed(info):
        nonlocal count
        count += 1
        identity = original(info)
        return (*identity[:-1], identity[-1] + (count > 1))
    monkeypatch.setattr(indexer, '_file_identity', changed)
    path.write_text('def new(): pass\n')
    indexer.build_index(project, db)
    assert rows(db) == before
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT name FROM symbols').fetchall() == [('old',)]


def test_failed_traversal_does_not_clean_existing_index(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'a.py').write_text('def a(): pass\n')
    db = tmp_path / 'index.sqlite3'
    indexer.build_index(project, db)
    before = rows(db)
    def fail(*args, **kwargs):
        kwargs['onerror'](PermissionError('scan denied'))
        return iter(())
    monkeypatch.setattr(os, 'walk', fail)
    with pytest.raises(PermissionError):
        indexer.build_index(project, db, force=True)
    assert rows(db) == before


def test_external_symlink_is_not_indexed(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    outside = tmp_path / 'outside.py'
    outside.write_text('def outside(): pass\n')
    (project / 'link.py').symlink_to(outside)
    db = tmp_path / 'index.sqlite3'
    indexer.build_index(project, db)
    assert rows(db) == {}


def test_invalid_utf8_does_not_replace_previous_symbols(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    path = project / 'a.py'
    path.write_text('def original(): pass\n')
    db = tmp_path / 'index.sqlite3'
    indexer.build_index(project, db)
    before = rows(db)
    path.write_bytes(b'\xff')
    indexer.build_index(project, db)
    assert rows(db) == before
