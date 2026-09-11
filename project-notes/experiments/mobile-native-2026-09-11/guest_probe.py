"""Run an offline script through the installed preview's real Binder/VM path."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import time

ADB = '/opt/homebrew/share/android-commandlinetools/platform-tools/adb'
PACKAGE = 'ai.cyrene.mobile.preview'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--script', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--repeat-in-guest', type=int, default=1)
    args = parser.parse_args()
    def adb(*command):
        return subprocess.run([ADB, '-s', 'emulator-5582', *command], capture_output=True, text=True, timeout=30)
    source = args.script.read_bytes()
    if args.repeat_in_guest > 1:
        source = ("import subprocess,sys,json\nresults=[]\n"
                  f"for i in range({args.repeat_in_guest}):\n"
                  f" p=subprocess.run([sys.executable,'-c',{source.decode()!r}],capture_output=True,text=True,timeout=60)\n"
                  " if p.returncode: raise RuntimeError(p.stderr)\n"
                  " results.append(json.loads(p.stdout))\n"
                  "print(json.dumps({'samples':results,'scope':'separate Python processes in one VM'}))\n").encode()
    script = base64.b64encode(source).decode()
    command = "python -c \"import base64; exec(compile(base64.b64decode('" + script + "'), 'research_probe', 'exec'))\""
    encoded = base64.b64encode(command.encode()).decode()
    report = {'script': args.script.name, 'scope': 'guest script via real Binder; first call includes VM mount', 'runs': []}
    for index in range(args.runs):
        adb('shell', 'run-as', PACKAGE, 'rm', '-f', 'files/runtime-integration-probe.json')
        began = time.perf_counter()
        launch = adb('shell', 'am', 'start', '-W', '-n', PACKAGE + '/ai.cyrene.mobile.RuntimeIntegrationProbeActivity', '--es', 'command_b64', encoded)
        row = {'index': index, 'launch': launch.stdout}
        while time.perf_counter() - began < 240:
            raw = adb('shell', 'run-as', PACKAGE, 'cat', 'files/runtime-integration-probe.json')
            if raw.returncode == 0:
                try:
                    result = json.loads(raw.stdout)
                except ValueError:
                    time.sleep(0.5)
                    continue
                row['wall_seconds'] = time.perf_counter() - began
                execution = result.get('execution', {})
                row['status'] = result.get('status')
                row['exit_code'] = execution.get('exit_code')
                row['stderr'] = execution.get('stderr', '')
                try:
                    row['result'] = json.loads(execution.get('stdout', ''))
                except ValueError:
                    row['error'] = result.get('message') or result.get('error') or execution.get('stdout') or 'missing JSON output'
                break
            time.sleep(0.5)
        else:
            row['error'] = 'probe timeout'
        report['runs'].append(row)
        args.output.write_text(json.dumps(report, indent=2))
        print(json.dumps(row), flush=True)
        if 'error' in row or row.get('exit_code') != 0:
            break


if __name__ == '__main__':
    main()
