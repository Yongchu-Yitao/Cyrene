"""Serve only the isolated synthetic display fixture on loopback."""
import http.server,json,sys,datetime,time
from urllib.parse import urlparse,parse_qs
from pathlib import Path
root=Path(sys.argv[1]).resolve()
log=Path(__file__).with_name('network-audit-results.jsonl')
class Handler(http.server.SimpleHTTPRequestHandler):
    audit_mode="complete"
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(root),**kwargs)
    def do_GET(self):
        if '/run-stream' in self.path:
            cursor=int(parse_qs(urlparse(self.path).query).get('cursor',['0'])[0])
            with Path(__file__).with_name('network-audit-requests.jsonl').open('a') as f:f.write(json.dumps({'cursor':cursor})+'\n')
            messages=[{'id':'net'+str(i),'role':'assistant','content':'网络消息 '+str(i),'createdAt':(datetime.datetime(2026,9,20,10,0,tzinfo=datetime.timezone.utc)+datetime.timedelta(seconds=80+i)).isoformat(),'timelineOrder':i,'timelineRevision':1,'timelineVersion':1,'status':'running' if i==39 else 'completed'} for i in range(40)]
            def patch(rev,**kwargs):return {'version':2,'runId':'network-audit','revision':rev,'status':'running',**kwargs}
            delta2={'type':'reply_delta','_seq':2,'timeline':patch(2,updates=[{'id':'net39','baseRevision':1,'append':{'content':'，断线前 🦊'},'set':{'timelineRevision':2}}])}
            if cursor==0:
                events=[{'type':'reply_start','_seq':1,'timeline':patch(1,snapshot=True,messages=messages)},delta2]
            else:
                messages[-1]['content']+='，断线前 🦊，恢复后 ✅';messages[-1]['status']='completed';messages[-1]['timelineRevision']=3
                messages=[x for x in messages if x['id']!='net38']
                events=[delta2,{'type':'reply_delta','_seq':3,'timeline':patch(3,updates=[{'id':'net39','baseRevision':2,'append':{'content':'，恢复后 ✅'},'set':{'timelineRevision':3}}])},{'type':'reply_delta','_seq':4,'timeline':patch(4,removedMessageIds=['net38'],messages=[])},{'type':'reply_done','_seq':5,'timeline':patch(5,snapshot=True,messages=messages,status='completed')},{'type':'saved','_seq':6,'assistantMessages':messages}]
            if cursor>0 and Handler.audit_mode!='complete':
                status='cancelled' if Handler.audit_mode=='interrupt' else 'failed'
                messages[-1]['status']=status
                events[-2]['timeline']['status']=status
                events[-1]={'type':'interrupted','_seq':6} if status=='cancelled' else {'type':'error','_seq':6,'message':'合成终止错误，用于等价性验证','code':'audit_terminal_failure'}
            self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.send_header('Connection','close');self.end_headers()
            for event in events:
                data=(json.dumps(event,ensure_ascii=False)+'\n').encode('utf-8')
                # Fragment UTF-8 and JSON across actual HTTP reads.
                for start in range(0,len(data),97):self.wfile.write(data[start:start+97]);self.wfile.flush()
                time.sleep(.08)
            self.close_connection=True
            return
        if self.path.startswith('/api/'):

            payload={'values':{'app_language':'en'}} if 'settings' in self.path else {'asr_ready':False,'tts_ready':False,'auto_read':False}
            data=json.dumps(payload).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data);return
        if self.path=='/fixture.txt':
            self.send_response(200);self.end_headers();self.wfile.write(b'synthetic fixture only');return
        super().do_GET()
    def do_POST(self):
        if self.path=='/audit-mode':
            payload=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))));Handler.audit_mode=payload['mode'];self.send_response(204);self.end_headers();return
        if self.path!='/audit-result':self.send_error(404);return
        data=self.rfile.read(int(self.headers.get('Content-Length','0')))
        result=json.loads(data)
        with log.open('a') as f:f.write(json.dumps(result)+'\n')
        self.send_response(204);self.end_headers()
    def log_message(self,*args):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(f'http://127.0.0.1:{server.server_port}',flush=True)
server.serve_forever()
