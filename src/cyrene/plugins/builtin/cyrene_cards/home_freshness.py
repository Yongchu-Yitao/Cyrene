"""Stable evidence and weather expiry for saved home sections."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

WEATHER_TTL = 60 * 60
RETRY_SECONDS = 120
DISPLAY_HOURS = (8, 12, 18, 22)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def evidence(chat, siblings, contexts=None):
    # Maintenance inputs and their generated replies must not wake maintenance again.
    messages, maintenance = [], False
    for message in chat.get("messages", []):
        if message.get("role") == "user":
            maintenance = message.get("agentMessageKind") == "welcome_refresh"
        if not maintenance:
            messages.append({key: message.get(key) for key in
                             ("id", "role", "content", "attachments", "trace", "modelStatus")})
    tasks = [{key: item.get(key) for key in ("id", "title", "status", "updatedAt", "activeGoal", "preview")}
             for item in siblings if item.get("id") != chat.get("id")
             and item.get("projectId") == chat.get("projectId")]
    return digest([messages, sorted(tasks, key=lambda item: str(item["id"])), chat.get("pendingQuestion"), contexts])


def weather_expired(cards, now):
    blocks = [block for card in cards if card["spec"].get("home_kind") == "overview"
              for block in card["spec"].get("blocks", []) if block.get("type") == "weather"]
    if not blocks:
        return True
    for block in blocks:
        try:
            stamp = datetime.fromisoformat(str(block.get("updated_at", "")).replace("Z", "+00:00"))
            # An undated or timezone-less observation is not verifiably current.
            if stamp.tzinfo is None:
                return True
            age = now - stamp.astimezone(timezone.utc).timestamp()
            if age < -300 or age >= WEATHER_TTL:
                return True
        except (ValueError, TypeError, OverflowError):
            return True
    return False


def display_slot(now):
    if now.hour < DISPLAY_HOURS[0]:
        return None
    return f"{now.date().isoformat()}:{max(hour for hour in DISPLAY_HOURS if hour <= now.hour):02}"


def preparation_target(now):
    """Latest display period due for preparation, including missed periods on startup."""
    ahead = now + timedelta(minutes=30)
    if ahead.hour < DISPLAY_HOURS[0]:
        return (ahead - timedelta(days=1)).replace(hour=DISPLAY_HOURS[-1], minute=0, second=0, microsecond=0)
    hour = max(hour for hour in DISPLAY_HOURS if hour <= ahead.hour)
    return ahead.replace(hour=hour, minute=0, second=0, microsecond=0)


def task_documents(port, owner):
    """Read durable task documents without opening an Agent or changing context."""
    from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
    from cyrene.core.context.tasks import STATE_KEY
    context = getattr(port, "context", None)
    if context is None:
        return {}
    directory = context.conversation_context.agent_states.context_directory
    if not (directory / "index.sqlite3").is_file():
        return {}
    try:
        with ContextStoreRouter(directory) as router:
            tree = router.get_tree(owner)
            root = router.get_node(tree.id, tree.root_id)
            state = root.value.get(STATE_KEY, {})
            return {key: {field: doc.get(field) for field in ("title", "summary", "body")}
                    for key, doc in state.get("documents", {}).items()}
    except TreeNotFoundError:
        return {}
