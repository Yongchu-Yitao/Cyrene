"""Short, nonblocking admission/maintenance exclusion in one Workbench host.

Like RunCoordinator, this boundary is shared across the host's worker threads
and event loops. Never block an event loop waiting for a synchronous clear.
"""
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

_locks: dict[tuple[str, str], Lock] = {}
_registry_lock = Lock()


@contextmanager
def session_admission(db_path: str, session_id: str):
    key = (str(Path(db_path).expanduser().resolve()), str(session_id))
    with _registry_lock:
        lock = _locks.setdefault(key, Lock())
        acquired = lock.acquire(blocking=False)
    if not acquired:
        from cyrene.workbench.sessions.session_presentation import WorkbenchSessionError
        raise WorkbenchSessionError(
            "Conversation admission or maintenance is in progress; retry shortly.",
            409, "conversation_admission_busy",
        )
    try:
        yield
    finally:
        with _registry_lock:
            lock.release()
            _locks.pop(key)
