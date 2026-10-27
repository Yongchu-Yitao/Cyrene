"""Serve the real Workbench from source in a disposable runtime for manual UI QA."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

async def serve(log_path: Path):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(2048)
    os.environ['WEB_PORT'] = str(listener.getsockname()[1])
    import httpx
    import uvicorn
    from cyrene.platform import config_store
    from cyrene.platform.bootstrap import initialize_runtime
    from cyrene.config import DB_PATH
    original_send = httpx.AsyncClient.send
    async def observed_send(client, request, **kwargs):
        if request.url.host != 'api.deepseek.com':
            return await original_send(client, request, **kwargs)
        started = time.monotonic()
        record = {'method': request.method, 'path': request.url.path}
        if request.method == 'POST':
            body = json.loads(request.content)
            record['parameters'] = {key: body[key] for key in ('model', 'query', 'count', 'need_content', 'url') if key in body}
        try:
            response = await original_send(client, request, **kwargs)
            record['status'] = response.status_code
            return response
        except Exception as exc:
            record['error_type'] = type(exc).__name__
            raise
        finally:
            record['seconds'] = round(time.monotonic() - started, 3)
            with log_path.open('a') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    httpx.AsyncClient.send = observed_send
    raw = config_store.get_setting('model_configuration', {})
    connection_ids = {c['id'] for c in raw['connections'] if (c.get('options') or {}).get('provider_preset') == 'deepseek' and c.get('api_key')}
    profiles = [p['id'] for p in raw['profiles'] if p['connection_id'] in connection_ids and p.get('enabled', True)]
    assert profiles, 'No official DeepSeek profile configured'
    raw['routes']['primary'] = profiles[:1]
    raw['routes']['secondary'] = profiles[:1]
    config_store.set_setting('model_configuration', raw)
    config_store.set_setting('app_language', 'zh-CN')
    await initialize_runtime(events=True, include_temp=True)
    from cyrene.workbench.webui.server import create_app, WebBot
    app = create_app(WebBot(), str(DB_PATH), instance_id='deepseek-computer-use-qa')
    print(f'UI_URL=http://127.0.0.1:{listener.getsockname()[1]}', flush=True)
    await uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', log_level='warning', loop='asyncio')).serve(sockets=[listener])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--http-log', required=True, type=Path)
    args = parser.parse_args()
    if args.worker:
        return asyncio.run(serve(args.http_log.resolve()))
    from cyrene.platform import config_store
    source = config_store.DATA_DIR
    before = hashlib.sha256((source/'config.enc').read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='cyrene-deepseek-ui-') as temp:
        data = Path(temp)/'data'
        data.mkdir(mode=0o700)
        for filename in ('config.enc', '.config_key'):
            shutil.copy2(source/filename, data/filename)
            (data/filename).chmod(0o600)
        args.http_log.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, 'CYRENE_BASE_DIR':temp, 'SEARXNG_AUTO_START':'0'}
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', '--http-log', str(args.http_log.resolve())], env=env, check=False)
    print(json.dumps({'source_configuration_unchanged': hashlib.sha256((source/'config.enc').read_bytes()).hexdigest() == before}), flush=True)
    return result.returncode

if __name__=='__main__':
    raise SystemExit(main())
