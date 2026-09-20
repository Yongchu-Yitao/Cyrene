"""Counterexamples for the narrow previous timing prototype; no product edits."""
import ast
import copy
import json
from pathlib import Path
from cyrene.workbench.chat.run_timeline import RunTimeline

root=Path(__file__).resolve().parent
tree=ast.parse((root/'timeline_probe.py').read_text())
functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'fixture','targeted_done'}]
ns={'RunTimeline':RunTimeline}
exec(compile(ast.Module(body=functions,type_ignores=[]),'<previous-prototype>','exec'),ns)
at='2026-09-20T00:00:01+00:00'
base={'type':'message.completed','messageId':'0','text':'final','timestamp':at}
cases={
    'ordinary_once':[base],
    'identical_completion_twice':[base,base],
    'duplicate_event_id':[{**base,'eventId':'same'}]*2,
    'response_precedes_text':[{**base,'response':'authoritative response'}],
    'null_text_preserves_content':[{**base,'text':None}],
    'numeric_text_coerced':[{**base,'text':7}],
    'unknown_source_creates_record':[{**base,'messageId':'unknown'}],
    'nested_payload_override':[{**base,'payload':{'text':'nested'}}],
}
results=[]
for name,events in cases.items():
    a=ns['fixture'](1); b=copy.deepcopy(a)
    steps=[]
    for event in events:
        old=a.apply(copy.deepcopy(event))
        try:
            new=ns['targeted_done'](b,copy.deepcopy(event))
            steps.append({'patch_equal':old==new,'state_equal':a.__dict__==b.__dict__,
                          'old_patch':old,'prototype_patch':new})
        except Exception as exc:
            steps.append({'patch_equal':False,'state_equal':False,'prototype_error':type(exc).__name__+': '+str(exc)})
    results.append({'case':name,'equivalent':all(x['patch_equal'] and x['state_equal'] for x in steps),'steps':steps})
assert results[0]['equivalent']
assert all(not x['equivalent'] for x in results[1:])
(root/'backend-recheck.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps([{k:v for k,v in x.items() if k!='steps'} for x in results],indent=2))
