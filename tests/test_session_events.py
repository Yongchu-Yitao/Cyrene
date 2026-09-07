import logging
from concurrent.futures import ThreadPoolExecutor

from cyrene.core.session import AgentSessionEvent
from cyrene.core.session_events import SessionEvents


def registry(replay=()):
    return SessionEvents("tree", lambda **_: replay, AgentSessionEvent, logging.getLogger(__name__))


def test_events_preserve_copy_sequence_listener_snapshot_and_failure_isolation():
    events = registry()
    events.initialize_sequence(7)
    seen = []
    unsubscribe = None

    def first(event):
        seen.append(("first", event.sequence))
        unsubscribe()

    events.subscribe(first)
    unsubscribe = events.subscribe(lambda event: seen.append(("second", event.sequence)))
    events.subscribe(lambda _: (_ for _ in ()).throw(RuntimeError("listener failure")))
    payload = {"nested": {"value": 1}}
    event = events.emit("assistant.completed", data=payload)
    payload["nested"]["value"] = 2
    assert event.data["nested"]["value"] == 1
    assert seen == [("first", 8), ("second", 8)]  # Snapshot captured before callbacks.
    events.emit("assistant.completed")
    assert seen[-1] == ("first", 9)
    assert events.sequence == 9
    unsubscribe()  # Idempotent.


def test_concurrent_publish_assigns_unique_sequences_without_locking_listener_callbacks():
    events = registry()
    # A listener can subscribe/unsubscribe reentrantly.
    events.subscribe(lambda _: events.subscribe(lambda event: None)())
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(pool.map(lambda _: events.emit("assistant.completed"), range(100)))
    assert sorted(event.sequence for event in result) == list(range(1, 101))
    assert events.sequence == 100


def test_replay_precedes_registration_and_replay_failure_does_not_register():
    old = registry().emit("assistant.completed")
    events = registry((old,))
    seen = []

    def replay_then_fail(event):
        seen.append(event)
        raise ValueError("replay failed")

    import pytest
    with pytest.raises(ValueError, match="replay failed"):
        events.subscribe(replay_then_fail, replay=True)
    events.emit("assistant.completed")
    assert seen == [old]
