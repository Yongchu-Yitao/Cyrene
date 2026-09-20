"""Validate completed, failed and cancelled actual HTTP replay comparisons."""
import gzip,json
from pathlib import Path
here=Path(__file__).resolve().parent
path=here/'network-audit-results.jsonl'
raw=path.read_text() if path.exists() else gzip.decompress(path.with_suffix('.jsonl.gz').read_bytes()).decode()
rows=[json.loads(line) for line in raw.splitlines()]
rows=[row for row in rows if row.get('networkEventCounts')==[16,16]]
result=[]
for row in rows:
    assert all(row[key] for key in ['networkEventsEqual','htmlEqual','textEqual','geometryEqual','actionsEqual','viewportEqual','editValuesEqual'])
    events=row['networkEvents'][0];final=events[-1]
    assert len(final['messages'])==119
    assert '恢复后 ✅' in final['messages'][-1]['content']
    assert sum('恢复后 ✅' in m['content'] for m in final['messages'])==1
    assert all(m['id']!='net38' for m in final['messages'])
    result.append({'status':final['messages'][-1]['status'],'events':len(events),'rows':row['rows'],'equal':True})
assert {r['status'] for r in result}>={'completed','failed','cancelled'}
(here/'network-summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(result)
