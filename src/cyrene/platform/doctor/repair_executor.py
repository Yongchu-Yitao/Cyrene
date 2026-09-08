"""Static verification everywhere; OS-isolated Python probes on macOS.

Never falls back to executing generated code on the host without a sandbox.
The source snapshot and probe are read-only; only an empty temporary directory
is writable. No host environment, network access, or user home is inherited.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

from .repair_workspace import materialize

OUTPUT_LIMIT = 16_000
PROBE_TIMEOUT = 15


def capabilities() -> dict:
    isolated = sys.platform == 'darwin' and not getattr(sys, 'frozen', False) and Path('/usr/bin/sandbox-exec').is_file()
    return {'mode': 'isolated_python' if isolated else 'static_only',
            'network': False, 'probe_timeout_seconds': PROBE_TIMEOUT}


def syntax_check(files: dict[str, bytes]) -> dict:
    errors = []
    for name, raw in files.items():
        if not name.endswith('.py'):
            continue
        try:
            ast.parse(raw, filename=name)
        except (SyntaxError, UnicodeError, ValueError, RecursionError) as exc:
            errors.append({'file': name, 'line': getattr(exc, 'lineno', None), 'type': type(exc).__name__})
    return {'status': 'failed' if errors else 'passed', 'errors': errors}


def _profile(source: Path, probe: Path, temporary: Path) -> str:
    # JSON quoting also safely quotes Seatbelt path strings.
    reads = {str(Path(sys.base_prefix).resolve()), str(Path(sys.prefix).resolve()),
             '/System', '/usr/lib', '/usr/share', '/Library/Apple/System/Library',
             str(source), str(probe), str(Path(__file__).resolve().parents[2])}
    rules = ['(version 1)', '(deny default)', '(allow sysctl-read)',
             '(allow process-exec (literal ' + json.dumps(str(Path(sys.executable).resolve())) + '))',
             '(allow file-read-metadata)',
             '(allow file-read* (literal "/"))',
             '(allow file-read* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))',
             '(allow file-write* (literal "/dev/null"))',
             '(allow file-read* file-write* (subpath ' + json.dumps(str(temporary)) + '))']
    rules.extend('(allow file-read* (subpath ' + json.dumps(path) + '))' for path in sorted(reads))
    return '\n'.join(rules)


async def run_probe(files: dict[str, bytes], probe: str) -> dict:
    if capabilities()['mode'] != 'isolated_python':
        return {'status': 'unavailable', 'reason': 'isolated_executor_unavailable'}
    if not isinstance(probe, str) or not probe.strip() or len(probe.encode()) > 16_000:
        raise ValueError('Provide a Python reproduction of at most 16000 bytes')
    with tempfile.TemporaryDirectory(prefix='cyrene-doctor-probe-') as raw_directory:
        directory = Path(raw_directory).resolve()
        source, temporary = directory / 'source', directory / 'tmp'
        materialize(source, files)
        temporary.mkdir()
        runner = directory / 'probe.py'
        runner.write_text(
            'import resource, sys, os\n'
            'resource.setrlimit(resource.RLIMIT_CPU, (10, 10))\n'
            'resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))\n'
            'resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))\n'
            'sys.path.insert(0, os.getcwd())\n'
            + 'sys.path.append(' + repr(str(Path(__file__).resolve().parents[3])) + ')\n'
            + 'print("CYRENE_PROBE_READY", flush=True)\n'
            + 'exec(compile(' + repr(probe) + ', "<doctor-reproduction>", "exec"))\n', encoding='utf-8')
        process = await asyncio.create_subprocess_exec(
            '/usr/bin/sandbox-exec', '-p', _profile(source, runner, temporary),
            sys.executable, '-I', '-B', str(runner),
            cwd=source, env={'PATH': '/usr/bin:/bin', 'HOME': str(temporary), 'TMPDIR': str(temporary), 'LANG': 'en_US.UTF-8'},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        output = bytearray()

        async def drain():
            while chunk := await process.stdout.read(4096):
                output.extend(chunk)
                if len(output) > OUTPUT_LIMIT:
                    raise ValueError('probe_output_limit')
            await process.wait()

        async def monitor_memory():
            # Darwin does not provide a usable RLIMIT_AS on all supported
            # runtimes. Monitor the one permitted process from outside the
            # sandbox instead; process-fork remains denied by Seatbelt.
            while process.returncode is None:
                monitor = await asyncio.create_subprocess_exec('/bin/ps', '-o', 'rss=', '-p', str(process.pid),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                try:
                    usage, _ = await monitor.communicate()
                finally:
                    if monitor.returncode is None:
                        monitor.kill()
                        await monitor.wait()
                if usage.strip() and int(usage.strip()) > 512 * 1024:
                    raise ValueError('probe_memory_limit')
                await asyncio.sleep(0.1)

        workers = [asyncio.create_task(drain()), asyncio.create_task(monitor_memory())]
        try:
            await asyncio.wait_for(asyncio.gather(*workers), PROBE_TIMEOUT)
        except (TimeoutError, ValueError) as exc:
            await _stop(process)
            return {'status': 'failed', 'reason': 'probe_timeout' if isinstance(exc, TimeoutError) else str(exc)}
        except asyncio.CancelledError:
            await asyncio.shield(_stop(process))
            raise
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        text = output.decode('utf-8', errors='replace')
        if not text.startswith('CYRENE_PROBE_READY\n'):
            return {'status': 'unavailable', 'reason': 'sandbox_start_failed', 'output': text[:2000]}
        return {'status': 'passed' if process.returncode == 0 else 'failed', 'exit_code': process.returncode,
                'output': text.removeprefix('CYRENE_PROBE_READY\n')}


async def _stop(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()
