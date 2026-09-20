"""Same-process pre-edit/current comparisons, temporary synthetic data only."""
import asyncio,gc,json,logging,os,sqlite3,statistics,tempfile,threading,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from equivalence import baseline,HERE

def timed(fn,n=7):
    values=[]
    for _ in range(n):
        start=time.perf_counter();fn();values.append((time.perf_counter()-start)*1000)
    return {'median_ms':statistics.median(values),'samples_ms':values}
def run(folder):
    from cyrene.workbench.chat.run_timeline import RunTimeline
    from cyrene.workbench.chat.chat_runs import ChatRunEventStore
    from cyrene.workbench.chat.context_read_cache import ContextReadCache
    from cyrene.plugins.builtin.cyrene_code.terminal.history import IncrementalPlainTextParser
    from cyrene.observability.debug_event_repository import DebugEventRepository
    from cyrene.core import observability
    from cyrene.core.context import tasks
    old={key:baseline(path,package) for key,path,package in [
      ('timeline','src/cyrene/workbench/chat/run_timeline.py','cyrene.workbench.chat'),
      ('events','src/cyrene/workbench/chat/chat_runs.py','cyrene.workbench.chat'),
      ('cache','src/cyrene/workbench/chat/context_read_cache.py','cyrene.workbench.chat'),
      ('terminal','src/cyrene/plugins/builtin/cyrene_code/terminal/history.py','cyrene.plugins.builtin.cyrene_code.terminal'),
      ('debug','src/cyrene/observability/debug_event_repository.py','cyrene.observability'),
      ('logging','src/cyrene/core/observability.py','cyrene.core'),
      ('tasks','src/cyrene/core/context/tasks.py','cyrene.core.context')]}
    report={}
    for name,cls in [('baseline',old['timeline'].RunTimeline),('candidate',RunTimeline)]:
        timeline=cls('r');at='2026-09-20T00:00:00+00:00'
        for i in range(5000):timeline._new('message',at,str(i)).update(content='x'*2048,status='completed')
        timeline.reply_id=next(reversed(timeline.records));count=0
        def complete():
            nonlocal count
            count+=1;assert len(timeline.apply({'type':'message.completed','messageId':'4999','text':str(count),'timestamp':at})['messages'])==1
        report['timeline_'+name]={'completion':timed(complete),'wait':timed(lambda:timeline.apply({'type':'permission.requested','timestamp':at}))}
    data=(b'plain terminal text '*60+b'\n')*1700
    outputs=[]
    for name,cls in [('baseline',old['terminal'].IncrementalPlainTextParser),('candidate',IncrementalPlainTextParser)]:
        def parse():
            p=cls();rows=[]
            for start in range(0,len(data),4096):rows+=p.feed(data[start:start+4096],start_seq=start)
            return rows,p.state()
        outputs.append(parse());report['terminal_'+name]=timed(parse,5)
    assert outputs[0]==outputs[1]
    logger=logging.getLogger('paired.disabled');logger.setLevel(logging.WARNING)
    for name,module in [('baseline',old['logging']),('candidate',observability)]:
        report['logging_'+name]=timed(lambda:[module.log_operation(logger,'probe','action',fields={'values':list(range(20))}) for _ in range(10000)])
    state={'active':None,'documents':{},'shared':{'body':''}}
    path=[SimpleNamespace(id=str(i),parent_id=str(i-1),value={'role':'user','content':'hello',tasks.STATE_KEY:{'archive':[{'id':j,'body':'x'*2048} for j in range(1000)]}}) for i in range(40)]
    assert old['tasks'].project_tasks(path,state)==tasks.project_tasks(path,state)
    for name,module in [('baseline',old['tasks']),('candidate',tasks)]:report['tasks_'+name]=timed(lambda:module.project_tasks(path,state),5)
    logdir=folder/'logs';logdir.mkdir();log=logdir/'debug_test.jsonl'
    with log.open('w') as f:
        for i in range(50000):f.write(json.dumps({'type':'llm_call','event_id':str(i),'timestamp':str(i).zfill(8),'messages':[{'content':'x'*1024}],'context_trace':{'total_tokens_est':512}})+'\n')
    outputs=[]
    for name,cls in [('baseline',old['debug'].DebugEventRepository),('candidate',DebugEventRepository)]:
        repo=cls(logdir,recent_events=lambda n:[],full_event=lambda _:None,subscribe_events=lambda **kw:None)
        report['debug_'+name]={'cold':timed(lambda:repo.context_events(120),1),'warm':timed(lambda:repo.context_events(120),5)};outputs.append(repo.context_events(120))
    assert outputs[0]==outputs[1]
    for name,cls in [('baseline',old['cache'].ContextReadCache),('candidate',ContextReadCache)]:
        cache=cls();paths=[folder/(name+str(i)+'.db') for i in range(2)]
        for p in paths:
            c=sqlite3.connect(p);c.execute('create table x(n)');c.close()
        cache.read(paths[0],lambda _:['warm'],lambda _:None);latencies=[]
        for i in range(5):
            entered=threading.Event()
            def slow(_):entered.set();time.sleep(.05);return ['slow']
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(cache.read,paths[1],slow,lambda _:None,variant=str(i));assert entered.wait(2)
                start=time.perf_counter();assert cache.read(paths[0],lambda _:['warm'],lambda _:None)==['warm'];latencies.append((time.perf_counter()-start)*1000);future.result()
        cache.close();report['cache_'+name]={'warm_hit_behind_50ms_miss_ms':latencies}
    # Actual workspace filesystem (not tmpfs), same durability settings.
    outputs=[]
    for name,cls in [('baseline',old['events'].ChatRunEventStore),('candidate',ChatRunEventStore)]:
        samples=[];fds=[]
        for trial in range(3):
            path=folder/(name+str(trial)+'events.db');store=cls(str(path));store.create(SimpleNamespace(run_id='r',chat_id='c',status='running',created_at='2026',seq=0,events=[]))
            gc.collect();start=time.perf_counter()
            for i in range(200):store.append_many('r',[{'_seq':i+2,'type':'reply_delta','delta':'x'*256}])
            samples.append((time.perf_counter()-start)*1000)
            handles=[]
            for f in Path('/proc/self/fd').iterdir():
                try:
                    if str(path) in os.readlink(f):handles.append(str(f))
                except OSError:pass
            fds.append(len(handles));gc.collect()
            c=sqlite3.connect(path);outputs.append(c.execute('select seq,event_json from workbench_chat_run_events order by seq').fetchall());c.close()
        report['events_'+name]={'samples_ms':samples,'open_db_fds_before_gc':fds}
    assert all(x==outputs[0] for x in outputs)
    (HERE/'paired-bench.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='paired-',dir=HERE) as d:
        os.environ['CYRENE_BASE_DIR']=d;run(Path(d))
