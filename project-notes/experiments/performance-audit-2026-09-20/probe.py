"""Read-only production investigation using temporary synthetic data; no product edits."""
import asyncio,copy,gc,hashlib,json,logging,os,platform,sqlite3,statistics,sys,tempfile,threading,time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
TMP=tempfile.TemporaryDirectory(prefix='cyrene-performance-audit-',dir=HERE if 'disk' in sys.argv else None)
RESULT='probe-disk-results.json' if 'disk' in sys.argv else 'probe-results.json'
os.environ['CYRENE_BASE_DIR']=TMP.name
logging.disable(logging.CRITICAL)
from cyrene.workbench.chat.run_timeline import RunTimeline
from cyrene.workbench.chat.chat_runs import ChatRunEventStore
from cyrene.workbench.chat.context_read_cache import ContextReadCache
from cyrene.observability.debug_event_repository import DebugEventRepository
from cyrene.core.context.store import ContextTreeStore

def measure(fn,n=15):
    values=[]
    for _ in range(n):
        t=time.perf_counter();fn();values.append((time.perf_counter()-t)*1000)
    return {'median_ms':statistics.median(values),'max_ms':max(values),'samples_ms':values}

def timeline():
    out=[]
    for n in [10,100,1000,5000]:
        t=RunTimeline('r');at='2026-09-20T00:00:00+00:00'
        for i in range(n):t._new('message',at,str(i)).update(content='x'*2048,status='completed')
        t.reply_id=next(reversed(t.records));t.records[t.reply_id]['status']='running'
        sequence=0
        def done():
            nonlocal sequence
            sequence+=1
            patch=t.apply({'type':'message.completed','messageId':str(n-1),'text':str(sequence),'timestamp':at})
            assert len(patch['messages'])==1
        def delta():
            patch=t.apply({'type':'message.delta','messageId':str(n-1),'delta':'x','timestamp':at})
            assert len(patch['updates'])==1
        out.append({'records':n,'completion':measure(done),'delta':measure(delta),'deepcopy_only':measure(lambda:copy.deepcopy(t.records)), 'permission_wait_no_record_change':measure(lambda:t.apply({'type':'permission.requested','timestamp':at}))})
    return out

def connections():
    results=[]
    for trial in range(3):
      for variant in (['current','closed','reused'] if trial%2==0 else ['reused','closed','current']):
        path=Path(TMP.name)/f'events-{trial}-{variant}.sqlite';store=ChatRunEventStore(str(path))
        store.create(SimpleNamespace(run_id='r',chat_id='c',status='running',created_at='2026',seq=0,events=[]))
        original=store._connect;calls=0;shared=original() if variant=='reused' else None
        def counted():
            nonlocal calls
            calls+=1;return original()
        @contextmanager
        def closed():
            conn=counted()
            try:
                with conn:yield conn
            finally:conn.close()
        @contextmanager
        def reused():
            with shared:yield shared
        store._connect=counted if variant=='current' else closed if variant=='closed' else reused
        def handles():
            total=0
            for f in Path('/proc/self/fd').iterdir():
                try:total+=str(path) in os.readlink(f)
                except OSError:pass
            return total
        gc.collect();t=time.perf_counter()
        for i in range(200):store.append_many('r',[{'_seq':i+2,'type':'reply_delta','delta':'x'*256}])
        ms=(time.perf_counter()-t)*1000
        fds=handles();gc.collect();after=handles()
        if shared:shared.close()
        with sqlite3.connect(path) as c:
            rows=c.execute('select seq,event_json from workbench_chat_run_events order by seq').fetchall()
            config={k:c.execute('pragma '+k).fetchone()[0] for k in ['journal_mode','synchronous']}
        assert len(rows)==200 and [r[0] for r in rows]==list(range(2,202))
        results.append({'trial':trial,'variant':variant,'ms':ms,'connections':calls+(variant=='reused'),'open_db_fds_before_gc':fds,'open_db_fds_after_gc':after,'rows':len(rows),'sha256':hashlib.sha256(json.dumps(rows).encode()).hexdigest(),'sqlite':config})
    assert len({r['sha256'] for r in results})==1
    return results

def cache():
    paths=[]
    for i in range(8):
        path=Path(TMP.name)/f'cache-{i}.sqlite';c=sqlite3.connect(path);c.execute('create table records(value text)');c.executemany('insert into records values (?)',[(json.dumps({'id':j,'content':'x'*512}),) for j in range(1000)]);c.commit();c.close();paths.append(path)
    obj=ContextReadCache();loads=0;loads_lock=threading.Lock()
    def read(path):
        def load(owner):
            nonlocal loads
            with loads_lock:loads+=1
            with sqlite3.connect(path) as c:result=[json.loads(x[0]) for x in c.execute('select value from records')]
            return result
        return obj.read(path,load,lambda _:None)
    t=time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:values=list(pool.map(read,paths))
    cold=(time.perf_counter()-t)*1000
    warm=measure(lambda:list(map(read,paths)),7)
    values[0][0]['content']='changed';assert read(paths[0])[0]['content']=='x'*512
    # Controlled one slow miss demonstrates cross-key head-of-line blocking.
    blocker=Path(TMP.name)/'cache-slow.sqlite';c=sqlite3.connect(blocker);c.execute('create table x(n)');c.close()
    hit_latencies=[]
    for i in range(5):
        entered=threading.Event()
        def slow(_):entered.set();time.sleep(.05);return ['synthetic blocking load']
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(obj.read,blocker,slow,lambda _:None,variant=str(i));assert entered.wait(2)
            start=time.perf_counter();read(paths[0]);hit_latencies.append((time.perf_counter()-start)*1000);future.result()
    obj.close()
    return {'eight_distinct_cold_ms':cold,'eight_sequential_hits':warm,'real_load_count':loads,'unrelated_hot_hit_behind_synthetic_50ms_miss_ms':hit_latencies,'copy_isolation':True}

async def debug():
    rows=[]
    for count in [1000,10000,50000]:
        folder=Path(TMP.name)/f'logs{count}';folder.mkdir();path=folder/'debug_20260920.jsonl'
        with path.open('w') as f:
            for i in range(count):f.write(json.dumps({'type':'llm_call','event_id':str(i),'timestamp':f'{i:010}','messages':[{'content':'x'*1024}],'context_trace':{'total_tokens_est':512,'included':[{}]}})+'\n')
            f.write('invalid json\n')
        repo=DebugEventRepository(folder,recent_events=lambda n:[],full_event=lambda _:None,subscribe_events=lambda **kw:None)
        samples=[]
        for mode in ['sync','thread','thread','sync']:
            ready=asyncio.Event();stop=asyncio.Event();lags=[]
            async def heartbeat():
                ready.set()
                while not stop.is_set():
                    start=time.perf_counter();await asyncio.sleep(.005);lags.append(max(0,(time.perf_counter()-start)*1000-5))
            task=asyncio.create_task(heartbeat());await ready.wait();await asyncio.sleep(.01)
            t=time.perf_counter();result=await asyncio.to_thread(repo.context_events,120) if mode=='thread' else repo.context_events(120);ms=(time.perf_counter()-t)*1000
            await asyncio.sleep(.01);stop.set();await task
            assert len(result['events'])==120 and result['events'][0]['id']==str(count-1)
            samples.append({'mode':mode,'wall_ms':ms,'max_heartbeat_lag_ms':max(lags),'result_sha256':hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()})
        assert len({x['result_sha256'] for x in samples})==1
        rows.append({'records':count,'bytes':path.stat().st_size,'samples':samples})
    return rows

def schema():
    output=[]
    query='SELECT 1 FROM context_nodes WHERE self_token_count IS NULL OR path_token_count IS NULL LIMIT 1'
    for n in [1000,10000,100000]:
        path=Path(TMP.name)/f'tree{n}.sqlite'
        store=ContextTreeStore.create(path,tree_id='tree',root_id='root',root_value={});store.close()
        c=sqlite3.connect(path)
        c.executemany('INSERT INTO context_nodes(node_id,parent_id,value_json,self_token_count,path_token_count,created_at,updated_at) VALUES (?, ?, ?, 1, 2, ?, ?)',[(str(i),'root',json.dumps({'content':'x'*512}),'2026','2026') for i in range(n)])
        c.commit()
        before_plan=c.execute('explain query plan '+query).fetchall();before=measure(lambda:c.execute(query).fetchone())
        opens=measure(lambda:ContextTreeStore(path).close(),7)
        # Experimental index exists solely on this disposable DB.
        c.execute('CREATE INDEX audit_missing_tokens ON context_nodes(node_id) WHERE self_token_count IS NULL OR path_token_count IS NULL');c.commit()
        after_plan=c.execute('explain query plan '+query).fetchall();after=measure(lambda:c.execute(query).fetchone())
        indexed_opens=measure(lambda:ContextTreeStore(path).close(),7)
        c.execute('update context_nodes set self_token_count=NULL where node_id=?',(str(n-1),));c.commit();assert c.execute(query).fetchone()==(1,)
        c.close();reopened=ContextTreeStore(path);assert reopened._connection.execute(query).fetchone() is None;reopened.close()
        output.append({'nodes':n,'baseline_query':before,'indexed_query':after,'baseline_open':opens,'indexed_open':indexed_opens,'before_plan':before_plan,'after_plan':after_plan,'missing_value_backfill_after_index':True})
    return output

sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'src/cyrene').rglob('*.py')}
report={'environment':{'python':sys.version,'platform':platform.platform(),'sqlite':sqlite3.sqlite_version},'source_before':sources}
try:
    for name,fn in ([('connections',connections)] if 'disk' in sys.argv else [('timeline',timeline),('connections',connections),('cache',cache),('debug',lambda:asyncio.run(debug())),('schema',schema)]):
        print('START',name,flush=True);report[name]=fn();(HERE/RESULT).write_text(json.dumps(report,indent=2)+'\n');print('DONE',name,flush=True)
finally:
    report['changed_sources']=[p for p,h in sources.items() if not (ROOT/p).exists() or hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
    (HERE/RESULT).write_text(json.dumps(report,indent=2)+'\n');TMP.cleanup()
