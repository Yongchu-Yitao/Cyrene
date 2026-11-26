"""Stable evidence and weather expiry for saved home sections."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

WEATHER_TTL = 60 * 60
RETRY_SECONDS = 120
DISPLAY_HOURS = (8, 12, 18, 22)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def recent_dialogue(chat):
    """Select real dialogue before limiting; activity rows must not consume slots."""
    messages, maintenance = [], False
    for message in chat.get("messages", []):
        role = message.get("role")
        if role == "user":
            maintenance = message.get("agentMessageKind") == "welcome_refresh"
        if maintenance or role not in {"user", "assistant"}:
            continue
        if any(message.get(key) for key in (
            "agentOriginated", "agentMessageKind", "systemInitiated", "intermediate",
            "intermediate_reply", "activityCard", "notificationCard",
        )):
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        messages.append({"role": role, "content": content, "createdAt": message.get("createdAt")})
    return messages[-20:]


def evidence(snapshot):
    # Refresh only when the same dialogue supplied to the model changes.
    return digest(snapshot)


def weather_expired(cards, now):
    blocks = [block for card in cards if card["spec"].get("home_kind") == "overview"
              for block in card["spec"].get("blocks", []) if block.get("type") in {"weather", "weather_observation"}]
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
