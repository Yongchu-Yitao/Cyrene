"""One event loop for guest ingress; no process is forked per HTTP connection."""
import asyncio


async def relay(reader, writer):
    while data := await reader.read(64 * 1024):
        writer.write(data)
        await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()


async def forward(client_reader, client_writer):
    upstream_writer = None
    tasks = []
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection('127.0.0.1', 4242), 15,
        )
        tasks = [asyncio.create_task(relay(client_reader, upstream_writer)),
                 asyncio.create_task(relay(upstream_reader, client_writer))]
        # An upstream EOF closes the tunnel. Client half-close still permits
        # the response to drain, including long-lived streaming responses.
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if tasks[1] not in done:
            tasks[0].result()
            await tasks[1]
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for writer in (client_writer, upstream_writer):
            if writer is not None:
                writer.close()
        await asyncio.gather(*(writer.wait_closed() for writer in (client_writer, upstream_writer)
                               if writer is not None), return_exceptions=True)


async def main():
    server = await asyncio.start_server(forward, '0.0.0.0', 4243, backlog=128)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
