from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_shielded_application_shutdown_finishes_when_host_is_cancelled():
    from cyrene.runtime.host import _shielded_application_shutdown

    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    class Application:
        async def shutdown(self) -> None:
            started.set()
            await release.wait()
            completed.set()

    task = asyncio.create_task(_shielded_application_shutdown(Application()))
    await started.wait()
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert completed.is_set()


def test_manual_web_mode_treats_keyboard_interrupt_as_normal_exit(monkeypatch):
    from cyrene.runtime import host

    monkeypatch.setattr(host, "_pick_web_port", lambda _preferred: 4242)

    def interrupt(coroutine):
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "run", interrupt)

    host._run_web_mode(ui_mode="workbench")
