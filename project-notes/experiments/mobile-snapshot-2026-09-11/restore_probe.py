"""Restore same complete backend, measure usable readiness and durable state."""
import json
import time
from probe import ROOT, PACKAGE, adb, launch, health

rows = []
expected = 'snapshot_v1'
for index in range(3):
    began, q, s = launch(True, 'ready' if index == 0 else 'latest')
    row = {'index': index, 'qmp_s': time.perf_counter()-began}
    q.s.settimeout(180)
    row['status'] = q.qmp('query-status')
    t = time.perf_counter()
    row['tool'] = s.execute("cat /workspace/snapshot-marker; printf '\\n'; cat /proc/sys/kernel/random/boot_id; systemctl show cyrene-desktop -p MainPID")
    row['first_tool_s'] = time.perf_counter()-t
    row['tool_ready_from_launch_s'] = time.perf_counter()-began
    assert row['tool']['stdout'].splitlines()[0] == expected, row
    deadline = time.monotonic()+60
    while True:
        try:
            row['health'] = health()
            break
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(.1)
    row['backend_ready_from_launch_s'] = time.perf_counter()-began
    expected = f'snapshot_v{index+2}'
    row['write'] = s.execute(f'printf "{expected}\\n" > /workspace/snapshot-marker; cat /workspace/snapshot-marker')
    row['sqlite'] = s.execute("python - <<'INNER'\nimport sqlite3\nc=sqlite3.connect('/workspace/snapshot-research.sqlite')\nc.execute('PRAGMA journal_mode=WAL')\nc.execute('PRAGMA synchronous=FULL')\nc.execute('CREATE TABLE IF NOT EXISTS events(value TEXT)')\nc.execute('INSERT INTO events VALUES (?)', ('" + expected + "',))\nc.commit()\nprint(c.execute('SELECT value FROM events ORDER BY rowid').fetchall())\nprint(c.execute('PRAGMA quick_check').fetchone())\nc.close()\nINNER")
    t = time.perf_counter()
    row['save'] = q.hmp('savevm latest')
    row['save_s'] = time.perf_counter()-t
    assert row['save'] == {'return': ''}, row['save']
    row['snapshots'] = q.hmp('info snapshots')
    rows.append(row)
    (ROOT/'restores.json').write_text(json.dumps(rows, indent=2))
    print(json.dumps(row, indent=2), flush=True)
    q.close()
    s.close()
adb('shell', 'am', 'force-stop', PACKAGE)
