import sys,json,tempfile,statistics,time,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'full-optimization-2026-09-20'))
from equivalence import baseline
HERE=Path(__file__).resolve().parent
from cyrene.plugins.builtin.cyrene_code.terminal.history import IncrementalPlainTextParser
from cyrene.observability.debug_event_repository import DebugEventRepository
old_parser=baseline('src/cyrene/plugins/builtin/cyrene_code/terminal/history.py','cyrene.plugins.builtin.cyrene_code.terminal').IncrementalPlainTextParser
old_repo=baseline('src/cyrene/observability/debug_event_repository.py','cyrene.observability').DebugEventRepository
report={}
for name,unit in [('ascii',b'hello terminal output\n'),('chinese','中文终端输出日志信息测试\n'.encode()),('ansi',b'\x1b[31mx\x1b[0m\n'),('mixed','normal 中文 output 🦊\n'.encode())]:
 data=(unit*(524288//len(unit)+1))[:524288];samples={key:[] for key in ['baseline','candidate']};outputs={}
 for round in range(6):
  for key,cls in ([('baseline',old_parser),('candidate',IncrementalPlainTextParser)] if round%2==0 else [('candidate',IncrementalPlainTextParser),('baseline',old_parser)]):
   p=cls();lines=[];start=time.perf_counter()
   for offset in range(0,len(data),4096):lines.extend(p.feed(data[offset:offset+4096],start_seq=offset))
   ms=(time.perf_counter()-start)*1000;outputs[key]=(lines,p.state())
   if round>0:samples[key].append(ms)
 assert outputs['baseline']==outputs['candidate']
 report[name]={'bytes':len(data),'median_ms':{k:statistics.median(v) for k,v in samples.items()},'samples':samples,'equal':True}
with tempfile.TemporaryDirectory() as temporary:
 folder=Path(temporary);path=folder/'debug_1.jsonl'
 events=[{'type':'llm_call','event_id':'old','timestamp':'2020','caller':'\ud800'},{'type':'llm_call','event_id':'new','timestamp':'2026','caller':'normal'}]
 path.write_text('\n'.join(json.dumps(e) for e in events)+'\n')
 outputs={}
 for key,cls in [('baseline',old_repo),('candidate',DebugEventRepository)]:
  repo=cls(folder,recent_events=lambda _:[],full_event=lambda _:None,subscribe_events=lambda **_:None)
  try:outputs[key]=repo.context_events(1)
  except Exception as e:outputs[key]={'exception':type(e).__name__,'message':str(e)}
 report['escaped_surrogate_outside_limit']=outputs
(HERE/'reproduction.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
