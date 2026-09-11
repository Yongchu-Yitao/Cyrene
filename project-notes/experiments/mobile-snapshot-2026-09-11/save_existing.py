"""Save initialized guest after excluding invalid HTTP startup pilot timings."""
import json
import time
from probe import ROOT, Channel, health
q = Channel(14544)
s = Channel(14545)
row = {'scope': 'Already initialized backend; startup timing excluded (wrong header in first pilot; system HTTP proxy in second).', 'health': health(), 'qmp': q.greeting}
row['tool'] = s.execute("printf snapshot_v1 > /workspace/snapshot-marker; cat /workspace/snapshot-marker; printf '\\n'; cat /proc/sys/kernel/random/boot_id; systemctl show cyrene-desktop -p MainPID")
print(row, flush=True)
q.s.settimeout(180)
t = time.perf_counter()
row['savevm'] = q.hmp('savevm ready')
row['save_s'] = time.perf_counter()-t
row['snapshots'] = q.hmp('info snapshots')
row['status'] = q.qmp('query-status')
(ROOT/'save.json').write_text(json.dumps(row, indent=2))
print(json.dumps(row, indent=2), flush=True)
