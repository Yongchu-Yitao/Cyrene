"""Collect only generated probe results from the dedicated research emulator."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import time

ADB = '/opt/homebrew/share/android-commandlinetools/platform-tools/adb'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', choices=['old', 'native'], required=True)
    parser.add_argument('--serial', default='emulator-5582')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=4)
    args = parser.parse_args()
    assert args.serial == 'emulator-5582', 'This collector is limited to the blank research AVD'
    def adb(*command):
        return subprocess.run([ADB, '-s', args.serial, *command], capture_output=True, text=True, timeout=30)
    if args.kind == 'old':
        package, activity, filename = 'ai.cyrene.mobile.runtime', '.RuntimeProbeActivity', 'probe-result.json'
        command = base64.b64encode(b'uname -m; printf RESEARCH_TOOL_OK').decode()
        extra = ['--es', 'command_b64', command]
    else:
        package, activity, filename = 'ai.cyrene.research.nativeprobe', 'ai.cyrene.research.ProbeActivity', 'native-probe.json'
        extra = []
    report = {'kind': args.kind, 'serial': args.serial, 'runs': []}
    for index in range(args.runs):
        adb('shell', 'am', 'force-stop', package)
        adb('shell', 'run-as', package, 'rm', '-f', 'files/' + filename)
        epoch = time.time()
        began = time.perf_counter()
        launch = adb('shell', 'am', 'start', '-W', '-n', package + '/' + activity, *extra)
        row = {'index': index, 'epoch': epoch, 'launch': launch.stdout.strip()}
        for _ in range(360):
            read = adb('shell', 'run-as', package, 'cat', 'files/' + filename)
            if read.returncode == 0:
                try:
                    row['result'] = json.loads(read.stdout)
                    row['wall_seconds_including_shutdown_and_poll'] = time.perf_counter() - began
                    break
                except ValueError:
                    pass
            time.sleep(0.5)
        else:
            row['error'] = 'probe timeout'
        log = adb('logcat', '-d', '-v', 'epoch', '-s', 'CyreneResearchTiming:I', '*:S').stdout
        row['operation_timings'] = []
        for line in log.splitlines():
            try:
                if float(line.split()[0]) >= epoch and '{' in line:
                    row['operation_timings'].append(json.loads(line[line.index('{'):]))
            except (ValueError, IndexError):
                pass
        report['runs'].append(row)
        args.output.write_text(json.dumps(report, indent=2))
        print(json.dumps(row), flush=True)
        if 'error' in row or 'error' in row.get('result', {}):
            break


if __name__ == '__main__':
    main()
