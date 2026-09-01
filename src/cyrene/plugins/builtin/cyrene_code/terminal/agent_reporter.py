"""Small fail-open bridge used by coding-agent lifecycle hooks.

Hook processes inherit the terminal identity and daemon state directory.  This
module deliberately uses only the standard library so every supported CLI can
call it without adding a runtime dependency::

    python -m cyrene.plugins.builtin.cyrene_code.terminal.agent_reporter \
        --agent kimi --event auto

The hook JSON is read from stdin.  Any reporting error returns success so a UI
status feature can never block the coding agent itself.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any


def _payload() -> dict[str, Any]:
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read(1024 * 1024)
        return dict(json.loads(raw)) if raw.strip() else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def report(agent: str, event: str, payload: dict[str, Any]) -> None:
    terminal_id = str(os.environ.get("CYRENE_TERMINAL_ID") or "").strip()
    state_root = str(os.environ.get("CYRENE_TERMINAL_STATE_DIR") or "").strip()
    if not terminal_id or not state_root:
        return
    connection = dict(json.loads(
        (Path(state_root).expanduser() / "connection.json").read_text(encoding="utf-8")
    ))
    inferred_event = str(event or "").strip()
    if inferred_event.casefold() in {"", "auto"}:
        inferred_event = str(
            payload.get("hook_event_name") or payload.get("event_name")
            or payload.get("event") or payload.get("type") or ""
        )
    request = {
        "version": int(connection.get("version") or 0),
        "token": connection.get("token"),
        "action": "agentEvent",
        "terminalId": terminal_id,
        "agentId": str(agent or payload.get("client_type") or ""),
        "event": inferred_event,
        "payload": payload,
    }
    encoded = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
    with socket.create_connection(
        ("127.0.0.1", int(connection.get("port") or 0)), timeout=0.35
    ) as client:
        client.sendall(encoded)
        client.settimeout(0.35)
        client.recv(4096)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--agent", default="")
    parser.add_argument("--event", default="auto")
    options = parser.parse_args()
    try:
        report(options.agent, options.event, _payload())
    except Exception:
        # Lifecycle hooks must remain observational and fail open.
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by agent CLIs
    raise SystemExit(main())


__all__ = ["main", "report"]
