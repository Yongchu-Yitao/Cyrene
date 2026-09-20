"""Serve only the isolated synthetic display fixture on loopback."""
import http.server,json,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
log=Path(__file__).with_name('browser-results.jsonl')
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(root),**kwargs)
    def do_GET(self):
        if self.path.startswith('/api/'):
            payload={'values':{'app_language':'en'}} if 'settings' in self.path else {'asr_ready':False,'tts_ready':False,'auto_read':False}
            data=json.dumps(payload).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data);return
        if self.path=='/fixture.txt':
            self.send_response(200);self.end_headers();self.wfile.write(b'synthetic fixture only');return
        super().do_GET()
    def do_POST(self):
        if self.path!='/audit-result':self.send_error(404);return
        data=self.rfile.read(int(self.headers.get('Content-Length','0')))
        result=json.loads(data)
        with log.open('a') as f:f.write(json.dumps(result)+'\n')
        self.send_response(204);self.end_headers()
    def log_message(self,*args):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(f'http://127.0.0.1:{server.server_port}',flush=True)
server.serve_forever()
