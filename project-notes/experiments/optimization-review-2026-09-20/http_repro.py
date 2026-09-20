import sys,json,tempfile
from pathlib import Path
from fastapi import FastAPI,APIRouter
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'full-optimization-2026-09-20'))
from equivalence import baseline
from cyrene.observability.debug_event_repository import DebugEventRepository
from cyrene.workbench.http.system.events import register_event_routes
old=baseline('src/cyrene/observability/debug_event_repository.py','cyrene.observability').DebugEventRepository
old_register=baseline('src/cyrene/workbench/http/system/events.py','cyrene.workbench.http.system').register_event_routes
results={}
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'debug_review.jsonl';p.write_text(json.dumps({'type':'llm_call','event_id':'older','timestamp':'2020','model':'\ud800'})+'\n'+json.dumps({'type':'llm_call','event_id':'latest','timestamp':'2026','model':'normal'})+'\n')
 for key,cls,register in [('baseline',old,old_register),('candidate',DebugEventRepository,register_event_routes)]:
  app=FastAPI();router=APIRouter();repo=cls(Path(d),recent_events=lambda _:[],full_event=lambda _:None,subscribe_events=lambda **_:None);register(router,repo);app.include_router(router)
  with TestClient(app,raise_server_exceptions=False) as client:
   response=client.get('/api/context-debug/events?limit=1');results[key]={'status':response.status_code,'body':response.text}
 assert results['baseline']['status']==200 and results['candidate']['status']==500
out=Path(__file__).with_name('http-reproduction.json');out.write_text(json.dumps(results,indent=2)+'\n');print(results)
