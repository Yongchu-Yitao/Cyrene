import sqlite3

from cyrene.workbench.chat.context_read_cache import ContextReadCache


def test_cache_tracks_wal_commits_dependencies_and_defensive_copies(tmp_path):
    path = tmp_path / 'tree.sqlite3'
    writer = sqlite3.connect(path)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('CREATE TABLE data(value TEXT)')
    writer.execute("INSERT INTO data VALUES ('first')")
    writer.commit()
    cache = ContextReadCache()
    calls = []
    owners = {'tool': ('pack', 'tool')}

    def load(owner):
        calls.append(1)
        return {'rows': [writer.execute('SELECT value FROM data').fetchone()[0]], 'owner': owner('tool')}

    try:
        first = cache.read(path, load, owners.get)
        first['rows'].append('caller mutation')
        assert cache.read(path, load, owners.get)['rows'] == ['first']
        assert len(calls) == 1
        writer.execute("UPDATE data SET value='second'")
        writer.commit()
        assert cache.read(path, load, owners.get)['rows'] == ['second']
        assert len(calls) == 2
        owners['tool'] = ('new', 'tool')
        assert cache.read(path, load, owners.get)['owner'] == ('new', 'tool')
        assert len(calls) == 3
    finally:
        cache.close()
        writer.close()


def test_oversized_entries_are_not_retained(tmp_path):
    path = tmp_path / 'tree.sqlite3'
    with sqlite3.connect(path) as writer:
        writer.execute('CREATE TABLE data(value TEXT)')
    cache = ContextReadCache(max_bytes=10)
    try:
        assert cache.read(path, lambda _: {'value': 'x' * 100}, lambda _: None)['value'] == 'x' * 100
        assert not cache._entries
        assert cache._bytes == 0
    finally:
        cache.close()
