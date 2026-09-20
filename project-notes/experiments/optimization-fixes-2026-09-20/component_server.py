"""Local-only component harness: current production React and Markdown rendering."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json
import mimetypes
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
APP=REPO/'src/cyrene/workbench/webui/static/app'
HTML='''<!doctype html><html data-theme="light"><meta charset="utf-8"><title>Cyrene isolated component benchmark</title>
<link rel="stylesheet" href="/app/fonts.css"><link rel="stylesheet" href="/app/shared/theme/base.css">
<link rel="stylesheet" href="/app/workbench.css"><link rel="stylesheet" href="/app/features/chat/chat.css">
<link rel="stylesheet" href="/app/features/chat/conversation.css"><link rel="stylesheet" href="/app/features/chat/viewer.css">
<style>body{margin:20px;background:#fff;color:#222}header{position:sticky;top:0;background:#fff;z-index:999;padding:12px}.wbc-thread{height:600px;overflow:auto}pre#results{white-space:pre-wrap}</style>
<header><h1>Cyrene component benchmark</h1><p>Isolated production transcript components; synthetic data; no model API.</p><button id="start">Run comprehensive browser test</button><strong id="status">Loading</strong></header>
<div class="workbench-shell" style="height:650px;display:block"><main id="transcript" class="wbc-thread"></main></div><pre id="results"></pre>
<script src="/app/react.production.min.js"></script><script src="/app/react-dom.production.min.js"></script>
<script src="/app/marked.min.js"></script><script src="/app/purify.min.js"></script>
<script>const services={i18n:{t:(key,params,fallback)=>fallback||key,getLang:()=>"en"},browser:{Icon:null},shortcuts:{},events:{subscribe:()=>()=>{}}};
window.CyreneUI={require:name=>{if(!(name in services))throw Error('Missing harness service '+name);return services[name]},register:(name,value)=>(services[name]=value)};</script>
<script src="/renderer.js"></script><script src="/browser-bundle.js"></script>'''

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        target=self.path.split('?')[0]
        if target in ('/baseline','/candidate'): data=HTML.replace('/browser-bundle.js','/'+target[1:]+'.js').replace("'/results'","'/results'").encode();kind='text/html'
        elif target=='/api/voice/status': data=json.dumps({'asr_ready':False,'tts_ready':False}).encode();kind='application/json'
        elif target=='/renderer.js': data=(REPO/'src/cyrene/workbench/webui/frontend/shared/markdown/renderer.jsx').read_bytes();kind='text/javascript'
        elif target in ('/baseline.js','/candidate.js'):data=(HERE/target[1:]).read_bytes();kind='text/javascript'
        elif target.startswith('/app/'):
            path=(APP/target[5:]).resolve()
            if not path.is_relative_to(APP) or not path.is_file():self.send_error(404);return
            data=path.read_bytes();kind=mimetypes.guess_type(path)[0] or 'application/octet-stream'
        else:self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type',kind);self.end_headers();self.wfile.write(data)
    def do_POST(self):
        if self.path!='/results':self.send_error(404);return
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        (HERE/('component-'+('baseline' if '/baseline' in self.headers.get('Referer','') else 'candidate')+'.json')).write_text(json.dumps(data,indent=2)+'\n')
        self.send_response(200);self.end_headers();self.wfile.write(b'OK')

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    print('http://127.0.0.1:'+str(server.server_address[1]),flush=True)
    server.serve_forever()
