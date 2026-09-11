"""Full installed preview startup in a newly created, credential-free AVD.

Attach to the already-started first-install run, then perform three app-force-
stop restarts. This includes guest recovery after abrupt process termination;
it is not a clean guest power-cycle. Never run against a user's phone.
"""
import json
from pathlib import Path
import subprocess
import time

ADB = '/opt/homebrew/share/android-commandlinetools/platform-tools/adb'
SERIAL = 'emulator-5582'
PACKAGE = 'ai.cyrene.mobile.preview'
ROOT = Path(__file__).resolve().parent


def adb(*args):
    return subprocess.run([ADB, '-s', SERIAL, *args], capture_output=True, text=True, timeout=30)


def main():
    report = {'serial': SERIAL, 'package': PACKAGE, 'runs': [],
              'scope': 'installed beta18 preview; not current source build; empty app data; no models'}
    since = 0
    for index in range(4):
        if index:
            adb('shell', 'am', 'force-stop', PACKAGE)
            since = time.time()
            launch = adb('shell', 'am', 'start', '-W', '-n', PACKAGE + '/ai.cyrene.mobile.DesktopWorkbenchActivity')
        row = {'index': index, 'scenario': 'first install' if index == 0 else 'app-force-stop restart',
               'launch': 'attached to initial run' if index == 0 else launch.stdout}
        began = time.perf_counter()
        while time.perf_counter() - began < 600:
            output = adb('logcat', '-d', '-v', 'epoch', '-s', 'CyreneStartup:I', '*:S').stdout
            lines = []
            for line in output.splitlines():
                try:
                    if float(line.split()[0]) >= since:
                        lines.append(line.strip())
                except (ValueError, IndexError):
                    pass
            row['stages'] = lines
            if any('workbench_page_loaded' in line for line in lines):
                row['page_loaded'] = True
                break
            if any('desktop_start error' in line for line in lines):
                row['error'] = 'desktop_start failed'
                break
            time.sleep(2)
        else:
            row['error'] = 'page timeout'
        row['runtime_memory'] = adb('shell', 'dumpsys', 'meminfo', PACKAGE + ':qemu').stdout
        report['runs'].append(row)
        (ROOT / 'preview-startup-results.json').write_text(json.dumps(report, indent=2))
        print(json.dumps({'index': index, 'page_loaded': row.get('page_loaded'), 'error': row.get('error'),
                          'stages': [line for line in row['stages'] if 'backend_service' not in line and 'State=' not in line and 'Status=' not in line]}), flush=True)
        if 'error' in row:
            break
    adb('shell', 'am', 'force-stop', PACKAGE)


if __name__ == '__main__':
    main()
