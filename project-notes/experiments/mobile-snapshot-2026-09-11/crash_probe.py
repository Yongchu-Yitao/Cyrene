"""Deliberately demonstrate stale snapshot rollback using disposable data only."""
import json
import time
from probe import ROOT, PACKAGE, adb, launch

report = {}
began, q, s = launch(True, 'latest')
report['before_write'] = s.execute('cat /workspace/snapshot-marker')
assert report['before_write']['stdout'].strip() == 'snapshot_v4'
report['committed_after_snapshot'] = s.execute("printf snapshot_v5_after_checkpoint > /workspace/snapshot-marker; python - <<'INNER'\nimport sqlite3\nc=sqlite3.connect('/workspace/snapshot-research.sqlite')\nc.execute('PRAGMA synchronous=FULL')\nc.execute('INSERT INTO events VALUES (?)', ('snapshot_v5_after_checkpoint',))\nc.commit()\nprint(c.execute('SELECT value FROM events ORDER BY rowid').fetchall())\nc.close()\nINNER")
q.close()
s.close()
# launch() force-stops the APK. No guest shutdown and no newer savevm.
began, q, s = launch(False)
while not s.line().startswith('CYRENE_VM_READY '):
    pass
report['cold_recovery_s'] = time.perf_counter()-began
check = "cat /workspace/snapshot-marker; python - <<'INNER'\nimport sqlite3\nc=sqlite3.connect('/workspace/snapshot-research.sqlite')\nprint(c.execute('SELECT value FROM events ORDER BY rowid').fetchall())\nprint(c.execute('PRAGMA quick_check').fetchone())\nc.close()\nINNER"
report['cold_recovered'] = s.execute(check)
assert report['cold_recovered']['stdout'].startswith('snapshot_v5_after_checkpoint')
q.close()
s.close()
began, q, s = launch(True, 'latest')
report['stale_restored'] = s.execute(check)
assert report['stale_restored']['stdout'].startswith('snapshot_v4')
assert 'snapshot_v5_after_checkpoint' not in report['stale_restored']['stdout']
report['conclusion'] = 'Uncheckpointed committed data survives cold recovery but is rolled back by loadvm latest. Production must invalidate consumed/stale checkpoints.'
(ROOT/'crash.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2), flush=True)
q.close()
s.close()
adb('shell', 'am', 'force-stop', PACKAGE)
