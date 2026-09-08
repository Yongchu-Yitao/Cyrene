"""Connection reuse without sharing clients across loops or configurations."""

import asyncio
import threading

import httpx
import pytest

from cyrene.model.http_clients import ModelHttpClients


class Client:
    def __init__(self):
        self.is_closed = False
        self.closed = asyncio.Event()
        self.close_loop = None

    async def aclose(self):
        self.close_loop = asyncio.get_running_loop()
        self.is_closed = True
        self.closed.set()


@pytest.mark.asyncio
async def test_reuses_real_connection_and_reconnects_after_server_close():
    connections = 0
    writers = set()
    tasks = set()

    async def serve(reader, writer):
        nonlocal connections
        connections += 1
        writers.add(writer)
        tasks.add(asyncio.current_task())
        try:
            while True:
                request = await reader.readuntil(b'\r\n\r\n')
                close = b'/close ' in request
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n'
                             + (b'Connection: close\r\n' if close else b'')
                             + b'\r\nok')
                await writer.drain()
                if close:
                    break
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            writers.discard(writer)
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    url = f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}'
    pool = ModelHttpClients()
    def factory():
        return httpx.AsyncClient(trust_env=False)
    try:
        for path in ['/', '/', '/close', '/']:
            async with pool.lease('connection', 'config', factory) as client:
                assert (await client.get(url + path)).text == 'ok'
        assert connections == 2
    finally:
        await pool.aclose()
        server.close()
        await server.wait_closed()
        for writer in tuple(writers):
            writer.close()
        if tasks:
            await asyncio.gather(*tuple(tasks))


@pytest.mark.asyncio
async def test_configuration_rotation_waits_for_active_request():
    pool = ModelHttpClients()
    async with pool.lease('same-connection', 'old-key', Client) as old:
        async with pool.lease('same-connection', 'new-key', Client) as new:
            assert new is not old
            assert not old.is_closed
        assert not old.is_closed
    await asyncio.wait_for(old.closed.wait(), 1)
    async with pool.lease('same-connection', 'new-key', Client) as reused:
        assert reused is new
    await pool.aclose()
    assert new.is_closed
    with pytest.raises(RuntimeError, match='closed'):
        async with pool.lease('same-connection', 'new-key', Client):
            pass


@pytest.mark.asyncio
async def test_idle_timer_closes_without_another_request():
    pool = ModelHttpClients(idle_seconds=0.01)
    async with pool.lease('c', 'k', Client) as first:
        pass
    await asyncio.wait_for(first.closed.wait(), 1)
    async with pool.lease('c', 'k', Client) as second:
        assert second is not first
    await pool.aclose()


@pytest.mark.asyncio
async def test_capacity_does_not_interrupt_active_clients():
    pool = ModelHttpClients(capacity=1)
    async with pool.lease('one', 'k', Client) as first:
        async with pool.lease('two', 'k', Client) as overflow:
            assert not first.is_closed
        await asyncio.wait_for(overflow.closed.wait(), 1)
    async with pool.lease('three', 'k', Client):
        await asyncio.wait_for(first.closed.wait(), 1)
    await pool.aclose()


@pytest.mark.asyncio
async def test_concurrent_leases_and_cancellation_release_client():
    pool = ModelHttpClients(idle_seconds=0.01)
    entered = asyncio.Event()
    held = []

    async def request():
        async with pool.lease('c', 'k', Client) as client:
            held.append(client)
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(request())
    await entered.wait()
    async with pool.lease('c', 'k', Client) as client:
        assert client is held[0]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not client.is_closed
    await asyncio.wait_for(client.closed.wait(), 1)
    await pool.aclose()


def test_separate_loops_close_clients_on_the_owner_loop():
    pool = ModelHttpClients()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever)
    thread.start()

    async def acquire():
        async with pool.lease('c', 'k', Client) as client:
            return client

    try:
        remote = asyncio.run_coroutine_threadsafe(acquire(), loop).result(timeout=2)

        async def main():
            local = await acquire()
            assert local is not remote
            await pool.aclose()
            assert local.close_loop is asyncio.get_running_loop()
            assert remote.close_loop is loop
        asyncio.run(main())
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()


@pytest.mark.asyncio
async def test_provider_reuses_client_and_rotates_credentials(monkeypatch):
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_model import _shared
    from cyrene.plugins.builtin.cyrene_model.anthropic import ANTHROPIC_PROVIDER

    pool = ModelHttpClients()
    clients = []

    async def endpoint(**kwargs):
        clients.append(kwargs['client'])
        return {'content': 'hi'}

    monkeypatch.setattr(_shared, '_complete_stream_endpoint', endpoint)
    ctx = PluginContext(services={'model_http_clients': pool}, data={
        'model_connection': {'id': 'test', 'api_key': 'first', 'base_url': 'https://test.invalid'},
    })
    args = {'model': 'MiniMax-M3', 'messages': [{'role': 'user', 'content': 'hi'}]}
    try:
        await _shared.complete_model(args, ctx, ANTHROPIC_PROVIDER)
        await _shared.complete_model(args, ctx, ANTHROPIC_PROVIDER)
        assert clients[0] is clients[1]
        ctx.data['model_connection']['api_key'] = 'second'
        await _shared.complete_model(args, ctx, ANTHROPIC_PROVIDER)
        assert clients[2] is not clients[0]
    finally:
        await pool.aclose()
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['base_url', 'proxy', 'timeout'])
async def test_provider_transport_configuration_isolation(monkeypatch, change):
    from cyrene.core.plugin import PluginContext
    from cyrene.plugins.builtin.cyrene_model import _shared
    from cyrene.plugins.builtin.cyrene_model.anthropic import ANTHROPIC_PROVIDER

    pool = ModelHttpClients()
    ctx = PluginContext(services={'model_http_clients': pool}, data={
        'model_connection': {'id': 'test'},
    })
    options = {'timeout': 10.0, 'trust_env': False}
    monkeypatch.setattr(_shared, '_client_options', lambda *a, **kw: dict(options))
    url = 'https://one.invalid'
    try:
        async with _shared._model_client(ctx, ANTHROPIC_PROVIDER, base_url=url, api_key='key') as old:
            if change == 'base_url':
                url = 'https://two.invalid'
            elif change == 'proxy':
                options['proxy'] = 'http://127.0.0.1:9'
            else:
                options['timeout'] = 20.0
            async with _shared._model_client(ctx, ANTHROPIC_PROVIDER, base_url=url, api_key='key') as new:
                assert new is not old
                assert not old.is_closed
    finally:
        await pool.aclose()
    assert old.is_closed and new.is_closed


@pytest.mark.asyncio
async def test_terminal_sse_drains_http_body_for_connection_reuse():
    from cyrene.model.protocol_adapters import PreparedRequest, handle_stream

    connections = 0
    tasks = set()

    async def serve(reader, writer):
        nonlocal connections
        connections += 1
        tasks.add(asyncio.current_task())
        try:
            while True:
                headers = await reader.readuntil(b'\r\n\r\n')
                length = next(int(line.split(b':')[1]) for line in headers.split(b'\r\n')
                              if line.lower().startswith(b'content-length:'))
                await reader.readexactly(length)
                body = (b'data: {"type":"content_block_delta","index":0,"delta":'
                        b'{"type":"text_delta","text":"hi"}}\n\n'
                        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n'
                        b'data: {"type":"message_stop"}\n\n')
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n'
                             b'Transfer-Encoding: chunked\r\n\r\n'
                             + f'{len(body):x}\r\n'.encode() + body + b'\r\n')
                await writer.drain()
                # HTTP EOF arrives after the model terminal event.
                await asyncio.sleep(0.01)
                writer.write(b'0\r\n\r\n')
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    url = f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}'
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            for _ in range(2):
                result = await handle_stream('anthropic', client, url, PreparedRequest({}, {}), None)
                assert result['content'] == 'hi'
        assert connections == 1
    finally:
        server.close()
        await server.wait_closed()
        if tasks:
            await asyncio.gather(*tuple(tasks))


@pytest.mark.asyncio
async def test_terminal_sse_does_not_wait_forever_for_http_eof():
    from cyrene.model.protocol_adapters import PreparedRequest, handle_stream

    class HangingTail(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\ndata: {"type":"message_stop"}\n\n'
            await asyncio.Future()

    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=HangingTail())
    )) as client:
        result = await asyncio.wait_for(handle_stream(
            'anthropic', client, 'https://test.invalid', PreparedRequest({}, {}), None,
        ), 1)
        assert result['finish_reason'] == 'end_turn'
