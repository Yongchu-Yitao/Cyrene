"""Real AgentSession concurrency, repeated turns, stream delivery and reopen probes."""
import asyncio
from collections import Counter
import gc
import json
import logging
import os
from pathlib import Path
import resource
import statistics
import sys
import tempfile
import threading
import time

HERE=Path(__file__).resolve().parent

async def main(root):
    from cyrene.core.session import AgentSession
    from cyrene.core.plugin import Plugin,PluginPack,PluginRegistry
    logging.disable(logging.CRITICAL)
    class Timed(AgentSession):
        def _prepare_model_input(self, trigger):
            start=time.perf_counter()
            value=super()._prepare_model_input(trigger)
            self.preparations.append({'ms':(time.perf_counter()-start)*1000,
                                      'messages':len(value.messages),'tokens':value.message_tokens})
            return value
    def create(directory, steps=5, chunks=100):
        counts={'models':0,'tools':0,'deltas':0,'texts':[]}
        events=Counter()
        async def model(arguments,context):
            counts['models']+=1
            turn_step=(counts['models']-1)%(steps+1)
            await asyncio.sleep(0.01)
            if turn_step<steps:
                return {'content':'','tool_calls':[{'id':f'c{counts["models"]}','name':'step','arguments':{'number':turn_step}}]}
            sink=context.services['model_stream']
            await sink({'type':'reply_start'})
            for i in range(chunks): await sink({'type':'reply_delta','delta':f'{i} '})
            text=''.join(f'{i} ' for i in range(chunks))
            await sink({'type':'reply_done','response':text})
            return {'content':text,'tool_calls':[]}
        async def tool(arguments,context):
            counts['tools']+=1
            return {'content':'x'*8192,'number':arguments['number']}
        def receive(event):
            events[event.type]+=1
            if event.type=='assistant.stream.delta':
                counts['deltas']+=1
                counts['texts'].append(event.data['delta'])
        registry=PluginRegistry()
        registry.register_pack(PluginPack('probe','probe',(
            Plugin('MiniMax','fixed',{'type':'object'},model,kind='model'),
            Plugin('step','fixed',{'type':'object','properties':{'number':{'type':'integer'}},'required':['number']},tool,
                   metadata={'permission_review':False}),)),source='experiment')
        session=Timed(directory/'data',directory/'workspace',directory/'plugins',registry=registry,
                      load_plugins=False,inherit_application_scope=False,event_listener=receive)
        session.preparations=[]
        return session,counts,events,registry
    async def workload(workers,turns,steps,chunks):
        base=root/f'w{workers}-t{turns}-s{steps}-c{chunks}'
        baseline_threads=threading.active_count()
        sessions=[create(base/str(i),steps,chunks) for i in range(workers)]
        lag=[];monitoring=True
        async def monitor():
            while monitoring:
                start=time.perf_counter();await asyncio.sleep(0.005)
                lag.append(max(0,(time.perf_counter()-start)*1000-5))
        monitor_task=asyncio.create_task(monitor())
        per_turn=[]
        async def run_session(i,session,counts):
            for turn in range(turns):
                start=time.perf_counter();before=len(session.preparations)
                session.submit('Complete the fixed task.',run_id=f'r{turn}')
                await asyncio.wait_for(session.drain(),60)
                output=session.final_output(f'r{turn}')
                assert output['content']==''.join(f'{i} ' for i in range(chunks))
                assert session.is_idle
                prepares=session.preparations[before:]
                per_turn.append({'worker':i,'turn':turn,'wall_ms':(time.perf_counter()-start)*1000,
                                 'prepare_ms':sum(p['ms'] for p in prepares),'last_input_tokens':prepares[-1]['tokens']})
            assert counts['models']==turns*(steps+1)
            assert counts['tools']==turns*steps
            assert counts['deltas']==turns*chunks
            assert ''.join(counts['texts'])==''.join(f'{i} ' for i in range(chunks))*turns
        start=time.perf_counter();cpu=time.process_time()
        try:
            await asyncio.gather(*(run_session(i,s,c) for i,(s,c,e,r) in enumerate(sessions)))
            elapsed=(time.perf_counter()-start)*1000;cpu_ms=(time.process_time()-cpu)*1000
        finally:
            monitoring=False;await monitor_task
            for session,*_ in sessions: session.close()
        # Reopen each real tree with recovery disabled; inspect the durable final output.
        reopen=[]
        for i,(_,counts,events,registry) in enumerate(sessions):
            directory=base/str(i);start=time.perf_counter()
            restored=AgentSession(directory/'data',directory/'workspace',directory/'plugins',registry=registry,
                                   load_plugins=False,inherit_application_scope=False,resume_on_restore=False)
            try:
                assert restored.final_output(f'r{turns-1}')['content']==''.join(f'{i} ' for i in range(chunks))
                assert counts['models']==turns*(steps+1), 'unexpected model rerun on reopen'
            finally:restored.close()
            reopen.append((time.perf_counter()-start)*1000)
        return {'workers':workers,'turns':turns,'steps':steps,'chunks':chunks,'wall_ms':elapsed,'cpu_ms':cpu_ms,
                'loop_lag_p95_ms':sorted(lag)[min(len(lag)-1,int(len(lag)*.95))], 'loop_lag_max_ms':max(lag),
                'per_turn':per_turn,'reopen_ms':reopen,'rss_highwater_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                'threads_before':baseline_threads,'threads_after_close':threading.active_count(),
                'thread_names_after_close':[t.name for t in threading.enumerate()],
                'event_counts':[dict(e) for s,c,e,r in sessions], 'checks_passed':True}
    results=[]
    for config in [(1,3,5,100),(4,3,5,100),(12,3,5,100),(1,40,1,100),(1,1,5,5000)]:
        print('CASE',config,flush=True)
        results.append(await workload(*config));gc.collect()
        print('RESULT',round(results[-1]['wall_ms'],2),'ms',flush=True)
    return results

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='cyrene-agent-matrix-') as temporary:
        os.environ['CYRENE_BASE_DIR']=temporary
        results=asyncio.run(main(Path(temporary)))
        suffix='-'+sys.argv[1] if len(sys.argv)>1 else ''
        (HERE/('agent-matrix'+suffix+'.json')).write_text(json.dumps(results,indent=2)+'\n')
        (HERE/('agent-matrix'+suffix+'-shutdown.json')).write_text(json.dumps({'thread_names_after_asyncio_shutdown':[t.name for t in threading.enumerate()]}))
