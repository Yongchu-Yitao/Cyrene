"""Differential oracle: exact pre-edit modules against current production."""
import copy,importlib.util,json,logging,os,random,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
HERE=Path(__file__).resolve().parent

def baseline(relative,package):
    name=package+'._optimization_baseline_'+Path(relative).stem
    spec=importlib.util.spec_from_file_location(name,HERE/'baseline'/relative)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module

def run():
    from cyrene.workbench.chat.run_timeline import RunTimeline
    from cyrene.plugins.builtin.cyrene_code.terminal.history import IncrementalPlainTextParser
    from cyrene.core.context import tasks
    old_timeline=baseline('src/cyrene/workbench/chat/run_timeline.py','cyrene.workbench.chat').RunTimeline
    old_parser=baseline('src/cyrene/plugins/builtin/cyrene_code/terminal/history.py','cyrene.plugins.builtin.cyrene_code.terminal').IncrementalPlainTextParser
    old_tasks=baseline('src/cyrene/core/context/tasks.py','cyrene.core.context')
    randomizer=random.Random(20260920);events=0;chunks=0
    kinds=['reply_start','reply_delta','reply_done','reasoning_start','reasoning_delta','reasoning_done','tool.started','tool.completed','permission.requested','permission.resolved','message.cancelled','notification','guidance_received','artifact.created','run.completed','run.failed','run.cancelled','unknown','intermediate_message']
    for stream in range(30):
        old,new=old_timeline('run'),RunTimeline('run');retained=[]
        for index in range(200):
            kind=randomizer.choice(kinds);source=str(randomizer.randrange(8))
            event={'type':kind,'timestamp':f'2026-09-20T00:{index//60:02}:{index%60:02}+00:00','messageId':source,'eventId':str(index if index%11 else max(0,index-1)), 'delta':'文🦊','text':randomizer.choice([None,42,'answer']),'toolCallId':'tool'+source,'name':'tool','status':'completed','attachment':{'id':'a'+source,'url':'/fixture'},'userMessage':{'id':'u'+source,'role':'user','content':'guidance'},'message':{'id':'i'+source,'role':'assistant','content':'intermediate'}}
            if index%13==0:event['payload']={'text':'nested override'}
            if index%17==0 and kind=='reply_start':event['reset']=True
            a=old.apply(copy.deepcopy(event));b=new.apply(copy.deepcopy(event));assert a==b,(stream,index,event,a,b)
            assert old.__dict__==new.__dict__,(stream,index,event)
            if index%23==0:retained.append((b,copy.deepcopy(b)))
            assert all(x==y for x,y in retained)
            events+=1
    atoms=[b'plain ASCII repeated ',b'\r',b'\n',b'\b',b'\x1b[31m',b'\x1b]133;A\x07',b'\x1b]title\x1b\\','中文🦊'.encode(),b'\xff\x80\xc2',bytes(range(32)),b'\x7f']
    for case in range(600):
        data=b''.join(randomizer.choice(atoms) for _ in range(30))
        old,new=old_parser(),IncrementalPlainTextParser();offset=0
        while offset<len(data):
            length=randomizer.randint(1,45);chunk=data[offset:offset+length]
            assert old.feed(chunk,start_seq=offset)==new.feed(chunk,start_seq=offset),(case,offset)
            assert old.state()==new.state() and old.current_line()==new.current_line(),(case,offset)
            if randomizer.randrange(5)==0:old,new=old_parser(old.state()),IncrementalPlainTextParser(new.state())
            offset+=len(chunk);chunks+=1
    for case in range(300):
        state={'active':'a' if case%3 else None,'documents':{'a':{'name':'A','body':'body','summary':'summary','messages':[],'covered':[]}},'shared':{'body':'shared'}}
        path=[SimpleNamespace(id='root',parent_id=None,value={'role':'system','content':'system',tasks.STATE_KEY:copy.deepcopy(state)})]
        for i in range(20):
            role='user' if i%2==0 else 'assistant';value={'role':role,'content':str(i),'task_context_id':'a' if (i+case)%3 else 'other'}
            if role=='assistant' and i%5==0:value['tool_calls']=[{'id':'call'+str(i),'name':'step','arguments':{'nested':[1,2]}}]
            path.append(SimpleNamespace(id=str(i),parent_id=path[-1].id,value=value))
        original=copy.deepcopy([x.value for x in path]);a=old_tasks.project_tasks(path,state);b=tasks.project_tasks(path,state)
        assert a==b,(case,a,b);assert [x.value for x in path]==original
        b[0]['content']='caller mutation';assert [x.value for x in path]==original
    result={'timeline_events':events,'terminal_cases':600,'terminal_chunks_and_restore_checks':chunks,'task_projection_cases':300,'equal':True}
    (HERE/'equivalence.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='cyrene-equivalence-') as d:
        os.environ['CYRENE_BASE_DIR']=d;logging.disable(logging.CRITICAL);run()
