import asyncio, json, time
from datetime import datetime, timezone
from pathlib import Path
from cyrene.core.plugin import PluginContext
from cyrene.model.http_clients import ModelHttpClients
from cyrene.plugins.builtin.cyrene_model.configuration import get_model_configuration
from cyrene.plugins.builtin.cyrene_model._shared import complete_model
from cyrene.plugins.builtin.cyrene_model.minimax import MINIMAX_PROVIDER
from cyrene.plugins.builtin.cyrene_model.openai_compatible import OPENAI_COMPATIBLE_PROVIDER
config=get_model_configuration(persist_seed=False)
out=Path(__file__).parent / 'app-results.jsonl'
def emit(r):
 line=json.dumps(r,ensure_ascii=False); print(line,flush=True)
 with out.open('a') as f: f.write(line+'\n')
async def run(model):
 p=next(p for p in config['profiles'] if p['model']==model)
 c=next(c for c in config['connections'] if c['id']==p['connection_id'])
 pool=ModelHttpClients()
 try:
  for case in ('short','padded','tools','short-repeat'):
   rec=dict(time=datetime.now(timezone.utc).isoformat(),model=model,case=case,mode='cyrene-pooled',success=False)
   started=time.monotonic()
   async def event(e): rec.setdefault('first_callback_seconds',round(time.monotonic()-started,3))
   async def trace(e):
    if e.get('type')=='response_end': rec['diagnostics']=e.get('diagnostics')
   ctx=PluginContext(data={'model_candidate':{'model':model},'model_timeout':180},services={'model_connection':c,'model_profile':p,'model_stream':event,'model_protocol_trace':trace,'model_http_clients':pool})
   args={'model':model,'messages':[{'role':'user','content':'你好。请只回复“你好”。'}],'max_tokens':512}
   if case=='padded': args['messages'].insert(0,{'role':'system','content':'Ignore this synthetic diagnostic context and answer briefly.\n'+'This is neutral diagnostic context.\n'*1800})
   if case=='tools':
    args['tools']=[{'type':'function','function':{'name':'diagnostic_echo','description':'Return the supplied text.','parameters':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}}]
    args['messages']=[{'role':'user','content':'Call diagnostic_echo with text hello.'}]
   emit({'event':'start','model':model,'case':case})
   try:
    async with asyncio.timeout(215):
     r=await complete_model(args,ctx,MINIMAX_PROVIDER if model=='MiniMax-M3' else OPENAI_COMPATIBLE_PROVIDER)
    rec.update(success=True,content_chars=len(r.get('content') or ''),tool_calls=len(r.get('tool_calls') or []),usage=r.get('usage'))
   except Exception as e:
    rec.update(error=type(e).__name__,error_detail=str(e).replace(c.get('api_key') or '__NO_KEY__','[redacted]'))
   rec['seconds']=round(time.monotonic()-started,3)
   emit(rec)
 finally: await pool.aclose()
async def main(): await asyncio.gather(run('MiniMax-M3'),run('minicpm5-2b-q8'))
asyncio.run(main())
