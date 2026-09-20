"""Verify that the latest browser run exercised the intended behavior, not no-ops."""
import json
import gzip
from pathlib import Path
here=Path(__file__).resolve().parent
log=here/'browser-results.jsonl'
raw=log.read_text() if log.exists() else gzip.decompress((here/'browser-results.jsonl.gz').read_bytes()).decode()
records=[json.loads(line) for line in raw.splitlines()]
run_id=max(x.get('runId',0) for x in records)
rows=[x for x in records if x.get('runId')==run_id and x.get('kind')=='comparison']
assert len(rows)==36,len(rows)
assert all(x['equal'] for x in rows),[(x['command'],x['differences']) for x in rows if not x['equal']]
assert not any(x.get('kind') in ('error','audit-error') for x in records if x.get('runId')==run_id)
by=lambda c:[x['right'] for x in rows if x['command']==c]
f=by('focus')[0]
assert f['focus']=='TEXTAREA' and f['focusPreserved'] and f['selectionPreserved']
assert f['inputs'][0]=={'value':'Unsent draft 保留','start':2,'end':8}
assert all(x['preserved']==120 for x in by('delta'))
assert by('delta')[1]['scroll']==200 and by('delta')[1]['priorScroll']==200
assert by('delta')[2]['selection']=='Keep selected text' and by('delta')[2]['selectionPreserved']
assert 'aria-expanded="true"' in by('expand')[0]['html']
assert by('copy')[0]['actions'][0]['name']=='copy'
assert len(by('edit')[0]['inputs'])==2 and by('edit-text')[0]['inputs'][0]['value']=='Exact edit text 中文'
assert len(by('cancel-edit')[0]['inputs'])==1
assert by('split')[0]['rows']==240 and by('unsplit')[0]['rows']==120
assert by('switch')[0]['rows']==1 and by('switch-back')[0]['rows']==120
assert by('remove')[0]['rows']==119
assert 'Read aloud' in by('voice-ready')[0]['html']
assert 'Read aloud' not in by('voice-off')[0]['html']
assert 'Copy updated label' in by('translations')[0]['html']
assert 'In-place scalar edit 文' in by('scalar-mutation')[0]['text']
assert '999 tokens' in by('metadata-change')[0]['text']
assert '888 tokens' in by('metadata-replace')[0]['text']
assert '888 tokens' not in by('metadata-remove')[0]['text']
animations=[a for x in rows for a in x['right']['animations']]
assert any('stream-fade' in a['class'] for a in animations)
assert any('retry-clearing' in a['class'] for a in animations)
assert rows[-1]['fastHits']>10
summary={'runId':run_id,'comparisons':len(rows),'equal':True,'fastHits':rows[-1]['fastHits'],'animationSamples':len(animations),'commands':[x['command'] for x in rows]}
(here/'browser-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
