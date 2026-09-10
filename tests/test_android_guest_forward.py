import asyncio
import importlib.util
from pathlib import Path


def test_guest_forward_streams_both_directions_and_drains_half_close(monkeypatch):
    path = Path(__file__).parents[1] / 'mobile/runtime-image/desktop/guest/forward.py'
    spec = importlib.util.spec_from_file_location('guest_forward', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def scenario():
        async def backend(reader, writer):
            try:
                while data := await reader.read(1024):
                    writer.write(data)
                    await writer.drain()
                writer.write(b'complete')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream = await asyncio.start_server(backend, '127.0.0.1', 0)
        target = upstream.sockets[0].getsockname()[1]
        original_open = asyncio.open_connection

        async def open_connection(host, port):
            return await original_open(host, target if port == 4242 else port)

        monkeypatch.setattr(module.asyncio, 'open_connection', open_connection)
        ingress = await asyncio.start_server(module.forward, '127.0.0.1', 0)
        async with upstream, ingress:
            reader, writer = await original_open('127.0.0.1', ingress.sockets[0].getsockname()[1])
            for data in (b'first', b'second'):
                writer.write(data)
                await writer.drain()
                assert await asyncio.wait_for(reader.readexactly(len(data)), 2) == data
            writer.write_eof()
            assert await asyncio.wait_for(reader.read(), 2) == b'complete'
            writer.close()
            await writer.wait_closed()

    asyncio.run(scenario())
