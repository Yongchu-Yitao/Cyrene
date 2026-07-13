"""Desktop App Use gateway backed by the Electron host accessibility bridge."""

from __future__ import annotations

import json
import os
import base64
from typing import Any

import httpx


def electron_app_use_available() -> bool:
    return bool(
        str(os.environ.get("CYRENE_ELECTRON_RPC_PORT") or "").strip()
        and str(os.environ.get("CYRENE_ELECTRON_RPC_TOKEN") or "").strip()
    )


async def _electron_app_rpc(
    operation: str,
    arguments: dict[str, Any] | None = None,
    *,
    timeout: float = 45.0,
) -> dict[str, Any]:
    port = str(os.environ.get("CYRENE_ELECTRON_RPC_PORT") or "").strip()
    token = str(os.environ.get("CYRENE_ELECTRON_RPC_TOKEN") or "").strip()
    if not port or not token:
        return {
            "status": "error",
            "type": "desktop_host_unavailable",
            "message": "App Use requires the Cyrene Electron desktop host.",
        }
    payload = {"method": str(operation or ""), "args": dict(arguments or {})}
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/app/rpc",
                headers={"X-Cyrene-Token": token, "Content-Type": "application/json"},
                content=json.dumps(payload, ensure_ascii=False),
            )
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        return {
            "status": "error",
            "type": "timeout",
            "message": "The desktop application did not respond before the App Use timeout.",
        }
    except Exception as exc:
        return {
            "status": "error",
            "type": "desktop_host_error",
            "message": f"App Use desktop bridge failed: {type(exc).__name__}: {exc}",
        }
    if not isinstance(data, dict):
        return {
            "status": "error",
            "type": "invalid_result",
            "message": "The App Use desktop bridge returned a non-object response.",
        }
    return data


def _validate_gateway_arguments(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    operation = str(arguments.get("operation") or "").strip()
    allowed = {"list_targets", "connect", "call", "status", "disconnect"}
    if operation not in allowed:
        raise ValueError(f"operation must be one of: {', '.join(sorted(allowed))}")
    parameters = arguments.get("parameters")
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")
    request: dict[str, Any] = {"parameters": parameters}
    for source, destination in (
        ("target_id", "target_id"),
        ("session_id", "session_id"),
        ("capability", "capability"),
    ):
        value = str(arguments.get(source) or "").strip()
        if value:
            request[destination] = value
    if operation == "call" and not request.get("session_id"):
        raise ValueError("session_id is required for call")
    if operation == "call" and not request.get("capability"):
        raise ValueError("capability is required for call")
    if operation in {"status", "disconnect"} and not request.get("session_id"):
        raise ValueError(f"session_id is required for {operation}")
    return operation, request


async def execute_app_use(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        operation, request = _validate_gateway_arguments(dict(arguments or {}))
    except ValueError as exc:
        return {"status": "error", "type": "invalid_arguments", "message": str(exc)}
    result = await _electron_app_rpc(operation, request)
    if (
        operation == "call"
        and request.get("capability") == "visual_describe"
        and result.get("status") == "success"
        and result.get("image_base64")
    ):
        image_base64 = str(result.pop("image_base64") or "")
        mime_type = str(result.get("mime_type") or "image/png")
        prompt = str(request.get("parameters", {}).get("prompt") or "").strip() or (
            "Describe this application window for a text-only agent. Extract visible text, controls, "
            "visual state, layout, alerts, charts, images, and anything needed to continue the task."
        )
        try:
            # Validate the payload before handing it to the model adapter.
            base64.b64decode(image_base64, validate=True)
            from cyrene.attachments import run_vision_chat

            vision = await run_vision_chat(
                [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
                    },
                ],
                content_prompt=prompt,
            )
            result["visual_observation"] = str(vision.get("vision_text") or "")
            result["vision_model"] = str(vision.get("vision_model") or "")
        except Exception as exc:
            return {
                "status": "error",
                "type": "vision_unavailable",
                "message": f"The application window was captured, but visual analysis failed: {type(exc).__name__}: {exc}",
                "session_id": result.get("session_id", ""),
            }
    return result


def format_app_use_result(result: dict[str, Any], *, max_chars: int = 20_000) -> str:
    """Serialize a compact structured observation without returning unbounded trees."""
    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= max_chars:
        return text
    compact = dict(result)
    nodes = compact.get("nodes")
    if isinstance(nodes, list):
        kept: list[Any] = []
        for node in nodes:
            candidate = {**compact, "nodes": [*kept, node], "truncated": True}
            rendered = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
            if len(rendered) > max_chars:
                break
            kept.append(node)
        compact["nodes"] = kept
        compact["truncated"] = True
        compact["truncation_reason"] = "tool_result_limit"
        text = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    verification = compact.get("verification")
    if len(text) > max_chars and isinstance(verification, dict) and isinstance(verification.get("nodes"), list):
        verification = dict(verification)
        verification["nodes"] = list(verification["nodes"])
        compact["verification"] = verification
        while verification["nodes"] and len(text) > max_chars:
            verification["nodes"].pop()
            verification["truncated"] = True
            verification["truncation_reason"] = "tool_result_limit"
            text = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        fallback = {
            "status": str(result.get("status") or "error"),
            "type": "result_too_large",
            "message": "The App Use observation exceeded the tool result limit. Request a smaller snapshot or subtree.",
        }
        return json.dumps(fallback, ensure_ascii=False, separators=(",", ":"))
    return text


__all__ = [
    "electron_app_use_available",
    "execute_app_use",
    "format_app_use_result",
]
