"""Bounded HTTP error inspection without publishing provider prose or prompts."""

import asyncio
import json
import re

import httpx


def _record_provider_diagnostics(payload, diagnostics):
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        if code in {
            "invalid_function_parameters", "invalid_request_error",
            "context_length_exceeded", "model_not_found",
            "invalid_api_key", "insufficient_quota", "rate_limit_exceeded",
        }:
            diagnostics["provider_error_code"] = code
        param = error.get("param")
        if isinstance(param, str) and re.fullmatch(
            r"tools\[\d{1,5}\]\.(?:parameters|name|strict)", param
        ):
            diagnostics["provider_error_param"] = param
        message = error.get("message")
        if isinstance(message, str) and "'uniqueItems' is not permitted" in message:
            diagnostics["provider_error_reason"] = "unsupported_schema_keyword:uniqueItems"


async def raise_for_model_status(response: httpx.Response, adapter: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        diagnostics = {
            "adapter": adapter,
            "http_status": response.status_code,
            "stream_completed": False,
            "termination_reason": "http_rejected",
        }
        # Error bodies can echo credentials or input. Publish only recognized
        # codes and structural locations, never the raw message/body.
        try:
            body = bytearray()
            async with asyncio.timeout(2):
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > 16384:
                        break
                    body.extend(chunk)
                else:
                    _record_provider_diagnostics(json.loads(body), diagnostics)
        except Exception:
            # Malformed, oversized, slow or broken bodies must not hide the
            # original HTTP failure or alter provider retry behavior.
            pass
        exc.diagnostics = diagnostics
        raise
