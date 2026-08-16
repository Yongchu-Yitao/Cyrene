"""Loopback OpenAI-compatible model gateway routes for external Agents."""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from cyrene.agent_runtime.model_gateway import (
    _openai_response,
    authorize_model_gateway,
    call_model_gateway,
)

logger = logging.getLogger(__name__)


def _responses_content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return str(value or "")
    parts: list[str] = []
    for part in value:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and str(part.get("type") or "") in {
            "input_text", "output_text", "text",
        }:
            parts.append(str(part.get("text") or ""))
    return "".join(parts)


def _responses_input_to_messages(value: Any) -> list[dict[str, Any]]:
    """Map OpenAI Responses input items to Cyrene's chat-shaped LLM API."""
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    if not isinstance(value, list):
        return [{"role": "user", "content": str(value or "")}]
    messages: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "message")
        if item_type == "function_call_output":
            messages.append({
                "role": "tool",
                "tool_call_id": str(item.get("call_id") or item.get("id") or ""),
                "content": _responses_content_text(item.get("output")),
            })
        elif item_type == "function_call":
            messages.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": str(item.get("call_id") or item.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or ""),
                        "arguments": str(item.get("arguments") or "{}"),
                    },
                }],
            })
        else:
            messages.append({
                "role": str(item.get("role") or "user"),
                "content": _responses_content_text(item.get("content")),
            })
    return messages


def _responses_tools(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    tools: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or str(item.get("type") or "") != "function":
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else None
        if function is None:
            function = {
                "name": str(item.get("name") or ""),
                "description": str(item.get("description") or ""),
                "parameters": item.get("parameters") if isinstance(item.get("parameters"), dict) else {},
            }
        tools.append({"type": "function", "function": function})
    return tools or None


def _responses_output(message: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    text = str(message.get("content") or "")
    if text:
        output.append({
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        })
    for call in message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        output.append({
            "type": "function_call",
            "id": str(call.get("id") or ""),
            "call_id": str(call.get("id") or ""),
            "name": str(function.get("name") or ""),
            "arguments": str(function.get("arguments") or "{}"),
            "status": "completed",
        })
    return output


def register_agent_model_gateway_routes(router: APIRouter) -> None:
    @router.post("/api/agent-model-gateway/v1/chat/completions", include_in_schema=False)
    async def api_agent_model_gateway_chat(request: Request):
        scope = authorize_model_gateway(str(request.headers.get("authorization") or ""))
        if scope is None:
            logger.info("gateway chat/completions rejected: missing or expired token")
            return JSONResponse({"error": {"message": "invalid or expired gateway token"}}, status_code=401)
        logger.info("gateway chat/completions authorized [chat=%s model=%s]", (scope or {}).get("chatId") or "?", (scope or {}).get("modelIdentity", {}).get("model") or "?")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            result = await call_model_gateway(body, scope)
            if result == "":
                return JSONResponse({"error": {"message": "Cyrene model is not configured", "code": "model_gateway_unavailable"}}, status_code=503)
            message = result if isinstance(result, dict) else {"role": "assistant", "content": str(result or "")}
            payload = _openai_response(message, str(body.get("model") or ""))
            if not body.get("stream"):
                return payload

            async def stream() -> AsyncIterator[str]:
                choice = payload["choices"][0]
                yield "data: " + json.dumps({
                    **payload,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": dict(choice["message"]), "finish_reason": choice["finish_reason"]}],
                }, ensure_ascii=False) + "\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(stream(), media_type="text/event-stream")
        except ValueError as exc:
            return JSONResponse({"error": {"message": str(exc)}}, status_code=400)
        except Exception as exc:
            kind = str(getattr(exc, "kind", "") or "model_request_failed")
            logger.exception(
                "External Agent chat-completions gateway request failed [%s]",
                kind,
            )
            return JSONResponse(
                {"error": {"message": "Cyrene model request failed", "code": kind}},
                status_code=502,
            )

    @router.post("/api/agent-model-gateway/v1/responses", include_in_schema=False)
    async def api_agent_model_gateway_responses(request: Request):
        scope = authorize_model_gateway(str(request.headers.get("authorization") or ""))
        if scope is None:
            logger.info("gateway /responses rejected: missing or expired token")
            return JSONResponse({"error": {"message": "invalid or expired gateway token"}}, status_code=401)
        logger.info("gateway /responses authorized [chat=%s model=%s]", (scope or {}).get("chatId") or "?", (scope or {}).get("modelIdentity", {}).get("model") or "?")
        try:
            body = await request.json()
            if logger.isEnabledFor(logging.INFO):
                logger.info("gateway /responses body received [model=%s input_blocks=%s]",
                            body.get("model"), len(body.get("input") or []))
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            messages = _responses_input_to_messages(body.get("input"))
            gateway_body = {
                **body,
                "messages": messages,
                "tools": _responses_tools(body.get("tools")),
                "max_tokens": body.get("max_output_tokens"),
            }
            result = await call_model_gateway(gateway_body, scope)
            if result == "":
                return JSONResponse({"error": {"message": "Cyrene model is not configured", "code": "model_gateway_unavailable"}}, status_code=503)
            message = result if isinstance(result, dict) else {"role": "assistant", "content": str(result or "")}
            text = str(message.get("content") or "")
            payload = {
                "id": f"resp_{secrets.token_hex(8)}",
                "object": "response",
                "created_at": int(time.time()),
                "status": "completed",
                "model": str(message.get("model") or body.get("model") or "cyrene-managed"),
                "output": _responses_output(message),
                "output_text": text,
                "usage": message.get("usage") if isinstance(message.get("usage"), dict) else {},
            }
            if not body.get("stream"):
                return payload

            async def stream() -> AsyncIterator[str]:
                yield "event: response.created\ndata: " + json.dumps({"type": "response.created", "response": {**payload, "status": "in_progress", "output": []}}, ensure_ascii=False) + "\n\n"
                if text:
                    yield "event: response.output_text.delta\ndata: " + json.dumps({"type": "response.output_text.delta", "delta": text}, ensure_ascii=False) + "\n\n"
                yield "event: response.completed\ndata: " + json.dumps({"type": "response.completed", "response": payload}, ensure_ascii=False) + "\n\n"

            return StreamingResponse(stream(), media_type="text/event-stream")
        except ValueError as exc:
            return JSONResponse({"error": {"message": str(exc)}}, status_code=400)
        except Exception as exc:
            kind = str(getattr(exc, "kind", "") or "model_request_failed")
            logger.exception(
                "External Agent responses gateway request failed [%s]",
                kind,
            )
            return JSONResponse(
                {"error": {"message": "Cyrene model request failed", "code": kind}},
                status_code=502,
            )


__all__ = ["register_agent_model_gateway_routes"]
