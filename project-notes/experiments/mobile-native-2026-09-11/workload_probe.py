"""Identical offline workloads for desktop Python and the Linux guest.

No network, no model, only a fresh TemporaryDirectory. This is a decomposition
experiment, not an end-to-end Agent benchmark. Do not sum overlapping metrics.
"""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time


def measured(fn, repeats):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return {'samples_ms': samples, 'median_ms': statistics.median(samples)}


def main():
    report = {'platform': platform.platform(), 'python': sys.version, 'metrics': {}}
    metrics = report['metrics']
    def command(args):
        subprocess.run(args, check=True, capture_output=True, timeout=60)
    metrics['python_process'] = measured(lambda: command([sys.executable, '-c', 'pass']), 5)
    try:
        metrics['jsonschema_process_import'] = measured(lambda: command([sys.executable, '-c', 'import jsonschema']), 3)
    except (subprocess.SubprocessError, OSError) as error:
        metrics['jsonschema_process_import'] = {'error': str(error)}
    payload = [{'name': 'tool_' + str(i), 'schema': {'type': 'object', 'properties': {
        'path': {'type': 'string'}, 'offset': {'type': 'integer'}}}} for i in range(500)]
    def json_work():
        for _ in range(50):
            assert len(json.loads(json.dumps(payload))) == 500
    metrics['json_500_tools_50_roundtrips'] = measured(json_work, 3)
    block = b'x' * (16 * 1024 * 1024)
    metrics['sha256_16MiB'] = measured(lambda: hashlib.sha256(block).digest(), 3)
    with tempfile.TemporaryDirectory(prefix='cyrene-research-') as temporary:
        root = Path(temporary)
        dbpath = root / 'events.sqlite3'
        with sqlite3.connect(dbpath) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY, body TEXT)')
            def transaction():
                db.execute('INSERT INTO events(body) VALUES (?)', ('sample-event',))
                db.commit()
            metrics['sqlite_full_wal_transaction'] = measured(transaction, 100)
            assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        files = [root / f'file-{i}.py' for i in range(500)]
        for file in files:
            file.write_text('value = 42\n' * 100)
        def scan():
            for file in files:
                file.stat()
                hashlib.sha256(file.read_bytes()).digest()
        metrics['stat_hash_500_files'] = measured(scan, 3)
        if shutil.which('git'):
            command(['git', 'init', '--quiet', str(root / 'repo')])
            metrics['git_status'] = measured(lambda: command(['git', '-C', str(root / 'repo'), 'status', '--porcelain']), 5)
    if shutil.which('bash'):
        metrics['bash_process'] = measured(lambda: command(['bash', '-c', 'printf ok']), 5)
    if shutil.which('node'):
        metrics['node_process'] = measured(lambda: command(['node', '-e', 'process.stdout.write("ok")']), 5)
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
