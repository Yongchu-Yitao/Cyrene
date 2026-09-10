import asyncio, json, time, socket, sys
from pathlib import Path
from datetime import datetime, timezone
import httpx
from cyrene.plugins.builtin.cyrene_model.configuration import get_model_configuration

OUT = Path(__file__).parent
config = get_model_configuration(persist_seed=False)
TARGETS = ('MiniMax-M3', 'minicpm5-2b-q8')

def emit(record):
    line = json.dumps(record, ensure_ascii=False)
    print(line, flush=True)
    with (OUT / 'results.jsonl').open('a') as f:
        f.write(line + '\n')

async def probe(model, mode='dual', size='short', repeat=1):
    profile = next(p for p in config['profiles'] if p['model'] == model)
    conn = next(c for c in config['connections'] if c['id'] == profile['connection_id'])
    endpoint = conn['base_url'].rstrip('/') + '/chat/completions'
    key = conn.get('api_key', '')
    host = httpx.URL(endpoint).host
    rec = dict(time=datetime.now(timezone.utc).isoformat(), model=model, mode=mode, size=size, repeat=repeat,
               status=None, chunks=0, done=False, finish_reasons=[], content_chars=0, reasoning_chars=0)
    started = time.monotonic()
    def elapsed(): return round(time.monotonic()-started, 3)
    last = None
    max_gap = 0
    try:
        rec['addresses'] = sorted(set(a[4][0] for a in await asyncio.to_thread(socket.getaddrinfo,host,None)))
        transport = httpx.AsyncHTTPTransport(**({'local_address':'0.0.0.0'} if mode=='ipv4' else {}))
        timeout = httpx.Timeout(180, connect=35)
        messages = [{'role':'user','content':'你好。请只回复“你好”。'}]
        if size == 'padded':
            messages.insert(0, {'role':'system','content':'The following is synthetic test context. Ignore it and answer the user briefly.\n' + 'This is neutral diagnostic context.\n'*1800})
        payload = dict(model=model,messages=messages,stream=True,max_tokens=256)
        if size == 'uncapped': payload.pop('max_tokens')
        emit(dict(event='start',model=model,mode=mode,size=size,repeat=repeat))
        async with asyncio.timeout(215):
            async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=timeout) as client:
                async with client.stream('POST',endpoint,headers={'Authorization':f'Bearer {key}'},json=payload) as response:
                    rec['status']=response.status_code
                    rec['headers_seconds']=elapsed()
                    if response.status_code != 200:
                        await response.aread()
                        rec['error']='HTTPStatusError'
                    else:
                        async for line in response.aiter_lines():
                            if not line.startswith('data:'): continue
                            now=time.monotonic()
                            if last is not None: max_gap=max(max_gap,now-last)
                            last=now
                            data=line[5:].strip()
                            if data=='[DONE]':
                                rec['done']=True
                                continue
                            try: item=json.loads(data)
                            except ValueError:
                                rec['invalid_json']=rec.get('invalid_json',0)+1
                                continue
                            rec['chunks']+=1
                            rec.setdefault('first_data_seconds',elapsed())
                            if 'error' in item: rec['provider_error']=True
                            if item.get('usage'): rec['usage']=item['usage']
                            for choice in item.get('choices',[]):
                                if choice.get('finish_reason'): rec['finish_reasons'].append(choice['finish_reason'])
                                delta=choice.get('delta',{})
                                rec['content_chars']+=len(delta.get('content') or '')
                                rec['reasoning_chars']+=len(delta.get('reasoning_content') or '')
    except Exception as exc:
        rec['error']=type(exc).__name__
        rec['error_detail']=str(exc).replace(key,'[redacted]') if key else str(exc)
        causes=[]
        cause=exc.__cause__
        while cause is not None and len(causes)<5:
            causes.append({'type':type(cause).__name__,'message':str(cause).replace(key,'[redacted]') if key else str(cause)})
            cause=cause.__cause__
        rec['causes']=causes
    rec['seconds']=elapsed()
    rec['max_data_gap_seconds']=round(max_gap,3)
    rec['complete']=rec['status']==200 and (rec['done'] or bool(rec['finish_reasons'])) and not rec.get('error') and not rec.get('provider_error')
    emit(rec)

async def main():
    mode=sys.argv[1] if len(sys.argv)>1 else 'dual'
    size=sys.argv[2] if len(sys.argv)>2 else 'short'
    models=sys.argv[3:] or TARGETS
    await asyncio.gather(*(probe(model,mode,size) for model in models))
asyncio.run(main())
