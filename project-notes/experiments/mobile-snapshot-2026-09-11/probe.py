"""Isolated emulator snapshot probe. Never run against a real/user device."""
import base64
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
ADB = '/opt/homebrew/share/android-commandlinetools/platform-tools/adb'
PACKAGE = 'ai.cyrene.research.snapshot'
TOKEN = 'snapshot_research_dummy_token_no_real_credentials_20260911'


def adb(*args, **kwargs):
    return subprocess.run([ADB, '-s', 'emulator-5582', *args], check=True, capture_output=True, **kwargs)


class Channel:
    def __init__(self, port, timeout=60):
        end = time.monotonic() + timeout
        while True:
            try:
                self.s = socket.create_connection(('127.0.0.1', port), timeout=2)
                self.s.settimeout(timeout)
                self.f = self.s.makefile('rwb', buffering=0)
                # adb's forwarded connection may accept before guest listener.
                if port == 14544:
                    self.greeting = json.loads(self.f.readline())
                    self.qmp('qmp_capabilities')
                break
            except (OSError, ValueError):
                if hasattr(self, 'f'):
                    self.f.close()
                if hasattr(self, 's'):
                    self.s.close()
                if time.monotonic() > end:
                    raise
                time.sleep(.1)

    def line(self):
        line = self.f.readline()
        if not line:
            raise EOFError('channel closed')
        return line.decode(errors='replace').strip()

    def qmp(self, command, arguments=None):
        request = {'execute': command}
        if arguments is not None:
            request['arguments'] = arguments
        self.f.write((json.dumps(request) + '\n').encode())
        while True:
            value = json.loads(self.line())
            if 'return' in value or 'error' in value:
                return value

    def hmp(self, command):
        return self.qmp('human-monitor-command', {'command-line': command})

    def execute(self, command, timeout=120):
        self.s.settimeout(timeout)
        rid = 'r' + str(time.monotonic_ns())
        self.f.write(f'CYRENE_EXEC {rid} {base64.b64encode(command.encode()).decode()}\n'.encode())
        while True:
            line = self.line()
            if line.startswith('CYRENE_RESULT ' + rid + ' '):
                _, _, code, out, err = line.split(' ', 4)
                result = {'code': int(code), 'stdout': '' if out == '-' else base64.b64decode(out).decode(),
                          'stderr': '' if err == '-' else base64.b64decode(err).decode()}
                if result['code']:
                    raise RuntimeError(result)
                return result

    def close(self):
        self.f.close()
        self.s.close()


def health():
    req = urllib.request.Request('http://127.0.0.1:14546/api/health', headers={'X-Cyrene-Token': TOKEN})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=2) as response:
        return {'status': response.status, 'body': response.read().decode()}


def launch(restore=False, snapshot='ready'):
    adb('shell', 'am', 'force-stop', PACKAGE)
    began = time.perf_counter()
    adb('shell', 'am', 'start', '-n', PACKAGE + '/ai.cyrene.research.MainActivity', '--ez', 'restore', str(restore).lower(), '--es', 'snapshot', snapshot)
    q = Channel(14544, 120)
    s = Channel(14545, 120)
    return began, q, s


def install():
    assert adb('emu', 'avd', 'name').stdout.splitlines()[0] == b'SnapshotResearch'
    adb('install', '-r', str(ROOT / 'harness/app/build/outputs/apk/debug/app-debug.apk'))
    adb('shell', 'run-as', PACKAGE, 'mkdir', '-p', 'files')
    for name in ['rootfs.qcow2', 'vmlinuz-virt', 'initramfs-cyrene', 'efi-virtio.rom']:
        start = time.perf_counter()
        with (Path('/tmp/cyrene-snapshot-image') / name).open('rb') as data:
            adb('shell', '-T', f"run-as {PACKAGE} sh -c 'cat > files/{name}'", stdin=data)
        print('installed', name, round(time.perf_counter()-start, 3), flush=True)
    for local, remote in [(14544,4544),(14545,4545),(14546,4546)]:
        adb('forward', f'tcp:{local}', f'tcp:{remote}')


def cold():
    began, q, s = launch()
    print('QMP', q.greeting, flush=True)
    lines = []
    while True:
        line = s.line()
        lines.append(line)
        if line.startswith('CYRENE_VM_READY '):
            break
    row = {'qmp': q.greeting, 'guest_ready_s': time.perf_counter()-began, 'boot_log': lines}
    (ROOT/'cold.json').write_text(json.dumps(row, indent=2))
    print('guest ready', row['guest_ready_s'], flush=True)
    print(s.execute('/opt/cyrene-mobile/desktop-control start ' + TOKEN), flush=True)
    deadline = time.monotonic() + 360
    while True:
        try:
            row['health'] = health()
            break
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(.5)
    row['backend_ready_s'] = time.perf_counter()-began
    print('backend ready', row['backend_ready_s'], flush=True)
    row['tool'] = s.execute("printf snapshot_v1 > /workspace/snapshot-marker; cat /workspace/snapshot-marker; cat /proc/sys/kernel/random/boot_id; systemctl show cyrene-desktop -p MainPID")
    print(row['tool'], flush=True)
    t = time.perf_counter()
    q.s.settimeout(180)
    row['savevm'] = q.hmp('savevm ready')
    row['save_s'] = time.perf_counter()-t
    row['snapshots'] = q.hmp('info snapshots')
    row['status'] = q.qmp('query-status')
    (ROOT/'cold.json').write_text(json.dumps(row, indent=2))
    print(json.dumps({k:v for k,v in row.items() if k!='boot_log'}, indent=2), flush=True)


if __name__ == '__main__':
    import sys
    {'install': install, 'cold': cold}[sys.argv[1]]()
