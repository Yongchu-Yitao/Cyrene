import asyncio

import pytest

from cyrene.observability import debug
from cyrene.workbench.chat.chat_events import publish_chat_changed


@pytest.fixture(autouse=True)
def reset_event_subscribers():
    debug._event_subscribers.clear()
    yield
    debug._event_subscribers.clear()


@pytest.mark.asyncio
async def test_event_bus_fans_out_to_every_subscriber():
    first = debug.subscribe()
    second = debug.subscribe()
    first_event = asyncio.create_task(anext(first))
    second_event = asyncio.create_task(anext(second))
    await asyncio.sleep(0)

    await debug.publish_event({"type": "session_update", "status": "running"})

    expected = {"type": "session_update", "status": "running"}
    assert {key: value for key, value in (await first_event).items() if key != "timestamp"} == expected
    assert {key: value for key, value in (await second_event).items() if key != "timestamp"} == expected
    await first.aclose()
    await second.aclose()


@pytest.mark.asyncio
async def test_session_filtering_happens_before_delivery():
    filtered = debug.subscribe(session_id="chat-2")
    event_task = asyncio.create_task(anext(filtered))
    await asyncio.sleep(0)

    await debug.publish_event({"type": "session_update"}, session_id="chat-1")
    await debug.publish_event({"type": "session_update", "status": "done"}, session_id="chat-2")

    event = await event_task
    assert event["session_id"] == "chat-2"
    assert event["status"] == "done"
    assert filtered.ag_frame.f_locals["queue"].empty()
    await filtered.aclose()


@pytest.mark.asyncio
async def test_sync_publisher_hands_worker_thread_event_to_host_loop():
    subscriber = debug.subscribe()
    event_task = asyncio.create_task(anext(subscriber))
    await asyncio.sleep(0)

    published = await asyncio.to_thread(
        debug.publish_event_sync,
        {"type": "notification_changed", "change": "created"},
    )

    assert published is True
    event = await event_task
    assert event["type"] == "notification_changed"
    assert event["change"] == "created"
    await subscriber.aclose()


@pytest.mark.asyncio
async def test_closing_subscription_unregisters_its_queue():
    subscriber = debug.subscribe(session_id="chat-1")
    event_task = asyncio.create_task(anext(subscriber))
    await asyncio.sleep(0)

    assert len(debug._event_subscribers) == 1
    event_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await event_task
    await subscriber.aclose()

    assert not debug._event_subscribers


@pytest.mark.asyncio
async def test_chat_change_helper_uses_one_canonical_envelope():
    subscriber = debug.subscribe(session_id="chat-1")
    event_task = asyncio.create_task(anext(subscriber))
    await asyncio.sleep(0)

    await publish_chat_changed(
        "chat-1",
        "project-1",
        "settled",
        run_status="completed",
        chatSummary={"id": "chat-1", "projectId": "project-1"},
    )

    event = await event_task
    assert event["type"] == "workbench_chat_changed"
    assert event["change"] == "settled"
    assert event["session_id"] == "chat-1"
    assert event["chat_id"] == "chat-1"
    assert event["project_id"] == "project-1"
    assert event["run_status"] == "completed"
    assert event["chatSummary"] == {"id": "chat-1", "projectId": "project-1"}
    await subscriber.aclose()
