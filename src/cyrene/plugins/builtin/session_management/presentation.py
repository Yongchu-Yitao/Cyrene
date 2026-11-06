"""Project known execution metadata into the assistant task interface."""


def task_result(value):
    """Translate known transport envelopes; nested evidence remains untouched."""
    if not isinstance(value, dict):
        return value
    names = {"session_id": "task_id", "sessions": "tasks",
             "message_id": "update_id", "pending_messages": "pending_updates",
             "queued_messages_paused": "queued_updates_paused"}
    result = {names.get(key, key): item for key, item in value.items()}
    if isinstance(value.get("sessions"), list):
        result["tasks"] = [task_result(item) for item in value["sessions"]]
    for key in ("loaded", "unloaded"):
        if isinstance(value.get(key), dict):
            result[key] = task_result(value[key])
    return result
