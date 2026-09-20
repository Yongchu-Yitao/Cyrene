import sys,json,tempfile,errno
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'full-optimization-2026-09-20'))
from equivalence import baseline
from cyrene.observability.debug_event_repository import DebugEventRepository
old=baseline('src/cyrene/observability/debug_event_repository.py','cyrene.observability').DebugEventRepository
out={}
with tempfile.TemporaryDirectory() as d:
 path=Path(d)/'debug_archived.jsonl';path.write_text(json.dumps({'type':'llm_call','event_id':'existing','timestamp':'2026'})+'\n')
 original=Path.open
 for name,cls in [('baseline',old),('candidate',DebugEventRepository)]:
  repo=cls(Path(d),recent_events=lambda _:[],full_event=lambda _:None,subscribe_events=lambda **_:None)
  def fail_once(self,*args,**kwargs):
   if self==path:raise OSError(errno.EMFILE,'synthetic transient descriptor exhaustion')
   return original(self,*args,**kwargs)
  before=path.stat()
  with patch.object(Path,'open',fail_once):first=repo.context_events(10)
  second=repo.context_events(10);after=path.stat()
  assert (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
  out[name]={'during_transient_failure':first,'after_recovery':second}
assert len(out['baseline']['after_recovery']['events'])==1 and out['candidate']['after_recovery']['events']==[]
Path(__file__).with_name('read-failure-reproduction.json').write_text(json.dumps(out,indent=2)+'\n');print(out)
