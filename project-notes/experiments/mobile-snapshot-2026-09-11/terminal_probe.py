"""Exercise the real backend terminal API and reconnect its WebSocket after restore."""
import base64
import json
import time
import urllib.request
from websockets.sync.client import connect
from probe import ROOT, TOKEN, PACKAGE, adb, launch, health


def api(path, payload=None):
    req = urllib.request.Request('http://127.0.0.1:14546'+path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={'X-Cyrene-Token': TOKEN, 'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as response:
        return json.load(response)


def ws(terminal_id):
    return connect('ws://127.0.0.1:14546/ws/terminals/'+terminal_id,
                   additional_headers={'X-Cyrene-Token': TOKEN}, proxy=None,
                   open_timeout=20, close_timeout=2)


def until(connection, marker):
    frames = []
    output = ''
    end = time.monotonic()+30
    while time.monotonic() < end:
        frame = connection.recv(timeout=max(.1,end-time.monotonic()))
        frames.append(frame)
        event = json.loads(frame)
        if event.get('type') == 'output':
            output += base64.b64decode(event['data']).decode(errors='replace')
        if marker in output:
            return {'frames': frames, 'decoded_output': output}
    raise TimeoutError(frames)


report = {}
_, q, s = launch(True, 'latest')
report['health_before'] = health()
report['project'] = api('/api/projects', {'name': 'Snapshot research'})
project_id = report['project']['project']['id']
report['created'] = api('/api/terminals', {'projectId': project_id, 'title': 'Snapshot research'})
terminal_id = report['created']['terminal']['id']
connection = ws(terminal_id)
report['initial_frame'] = connection.recv(timeout=20)
connection.send(json.dumps({'type': 'input', 'data': "export SNAPSHOT_RESEARCH=memory_keep_20260911; printf 'BEFORE_%s\\n' \"$SNAPSHOT_RESEARCH\"\n"}))
report['before_frames'] = until(connection, 'BEFORE_memory_keep_20260911')
connection.close()
q.s.settimeout(180)
report['save'] = q.hmp('savevm terminal')
assert report['save'] == {'return': ''}
q.close()
s.close()
began, q, s = launch(True, 'terminal')
report['health_after'] = health()
report['terminal_after'] = api('/api/terminals?projectId='+project_id)
connection = ws(terminal_id)
report['reconnect_frame'] = connection.recv(timeout=20)
connection.send(json.dumps({'type': 'input', 'data': "printf 'AFTER_%s\\n' \"$SNAPSHOT_RESEARCH\"\n"}))
report['after_frames'] = until(connection, 'AFTER_memory_keep_20260911')
report['restore_to_terminal_command_s'] = time.perf_counter()-began
connection.close()
q.close()
s.close()
(ROOT/'terminal.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2),flush=True)
adb('shell','am','force-stop',PACKAGE)
