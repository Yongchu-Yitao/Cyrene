"""Provider-specific balance and subscription quota telemetry.

Protocol adapters deliberately only describe model wire formats.  This module
is the separate compatibility boundary for account-level provider features
that happen to use the same credential as a model connection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import math
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

_CACHE_TTL_SECONDS = 60.0
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_REFRESH_TASKS: dict[str, asyncio.Task[dict[str, Any]]] = {}


class ProviderTelemetryError(RuntimeError):
    """A provider rejected or returned invalid account telemetry."""


@dataclass(frozen=True)
class ProviderTelemetryDefinition:
    provider: str
    label: str
    hosts: frozenset[str]


_DEFINITIONS = {
    "deepseek": ProviderTelemetryDefinition(
        provider="deepseek",
        label="DeepSeek",
        hosts=frozenset({"api.deepseek.com"}),
    ),
    "minimax": ProviderTelemetryDefinition(
        provider="minimax",
        label="MiniMax",
        hosts=frozenset({"api.minimax.io", "api.minimaxi.com"}),
    ),
}


def _provider_preset(connection: dict[str, Any]) -> str:
    options = connection.get("options")
    return str(
        options.get("provider_preset") if isinstance(options, dict) else ""
    ).strip().lower()


def _official_origin(
    connection: dict[str, Any], definition: ProviderTelemetryDefinition
) -> str:
    parsed = urlsplit(str(connection.get("base_url") or ""))
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or host not in definition.hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise ProviderTelemetryError(
            "Account usage is available only for an official provider endpoint"
        )
    return f"https://{host}"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _percent(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return max(0.0, min(100.0, _number(value)))


def _reset_at(milliseconds: Any) -> str | None:
    remaining = _number(milliseconds, -1)
    if remaining < 0 or remaining > 10 * 365 * 24 * 60 * 60 * 1000:
        return None
    return (
        datetime.now(timezone.utc) + timedelta(milliseconds=remaining)
    ).isoformat()


def _normalize_deepseek(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("balance_infos"), list):
        raise ProviderTelemetryError("DeepSeek returned an invalid balance response")
    balances = []
    for raw in payload["balance_infos"]:
        if not isinstance(raw, dict):
            continue
        balances.append({
            "currency": str(raw.get("currency") or "").upper(),
            "total": str(raw.get("total_balance") or "0"),
            "granted": str(raw.get("granted_balance") or "0"),
            "topped_up": str(raw.get("topped_up_balance") or "0"),
        })
    return {
        "kind": "balance",
        "available": payload.get("is_available") is True,
        "balances": balances,
        "windows": [],
    }


def _minimax_window(
    raw: dict[str, Any],
    *,
    kind: str,
    remaining_key: str,
    time_key: str,
    status_key: str,
    unlimited_key: str,
    total_key: str,
    count_key: str,
) -> dict[str, Any]:
    remaining = _percent(raw.get(remaining_key))
    total = _number(raw.get(total_key))
    count = _number(raw.get(count_key))
    # Older count-based plans do not always return explicit percentages.  The
    # API's *_usage_count has historically represented remaining count despite
    # its name, so use it directly rather than subtracting it from total.
    if remaining is None and total > 0:
        remaining = max(0.0, min(100.0, count / total * 100.0))
    status = int(_number(raw.get(status_key)))
    unlimited = raw.get(unlimited_key) is True
    return {
        "kind": kind,
        "remaining_percent": remaining,
        "used_percent": None if remaining is None else 100.0 - remaining,
        "reset_at": _reset_at(raw.get(time_key)),
        "status": status,
        # MiniMax's undocumented status=3 is ambiguous: the official CLI has
        # rendered it as unlimited, while provider issue reports show the same
        # value for 0/0 resources that are not included in a plan.  Never turn
        # it into an unlimited claim without an explicit upstream flag.
        "unlimited": unlimited,
        "ambiguous": status == 3 and not unlimited,
        "remaining_count": count if total > 0 else None,
        "total_count": total if total > 0 else None,
    }


def _normalize_minimax(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProviderTelemetryError("MiniMax returned an invalid quota response")
    base_response = payload.get("base_resp")
    if isinstance(base_response, dict):
        status_code = int(_number(base_response.get("status_code"), -1))
        if status_code != 0:
            message = str(base_response.get("status_msg") or "MiniMax rejected the quota request")
            raise ProviderTelemetryError(message)
    rows = payload.get("model_remains")
    if not isinstance(rows, list):
        raise ProviderTelemetryError("MiniMax returned an invalid quota response")
    windows = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        model = str(raw.get("model_name") or "general")
        current = _minimax_window(
            raw,
            kind="interval",
            remaining_key="current_interval_remaining_percent",
            time_key="remains_time",
            status_key="current_interval_status",
            unlimited_key="current_interval_unlimited",
            total_key="current_interval_total_count",
            count_key="current_interval_usage_count",
        )
        weekly = _minimax_window(
            raw,
            kind="weekly",
            remaining_key="current_weekly_remaining_percent",
            time_key="weekly_remains_time",
            status_key="current_weekly_status",
            unlimited_key="current_weekly_unlimited",
            total_key="current_weekly_total_count",
            count_key="current_weekly_usage_count",
        )
        current["model"] = model
        weekly["model"] = model
        windows.extend((current, weekly))
    return {
        "kind": "quota",
        "available": bool(windows),
        "balances": [],
        "windows": windows,
    }


async def _request_provider(
    connection: dict[str, Any], definition: ProviderTelemetryDefinition
) -> dict[str, Any]:
    api_key = str(connection.get("api_key") or "").strip()
    if not api_key:
        return {
            "connection_id": connection.get("id"),
            "provider": definition.provider,
            "label": definition.label,
            "kind": "balance" if definition.provider == "deepseek" else "quota",
            "status": "unconfigured",
            "available": False,
            "balances": [],
            "windows": [],
        }
    origin = _official_origin(connection, definition)
    endpoint = (
        f"{origin}/user/balance"
        if definition.provider == "deepseek"
        else f"{origin}/v1/token_plan/remains"
    )
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    timeout = httpx.Timeout(10.0, connect=5.0)
    from cyrene.runtime.network_proxy import configured_proxy_url

    proxy_url = configured_proxy_url(opt_in=connection.get("use_proxy") is True)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        proxy=proxy_url or None,
    ) as client:
        response = await client.get(endpoint, headers=headers)
        response.raise_for_status()
        payload = response.json()
    normalized = (
        _normalize_deepseek(payload)
        if definition.provider == "deepseek"
        else _normalize_minimax(payload)
    )
    return {
        "connection_id": connection.get("id"),
        "provider": definition.provider,
        "label": definition.label,
        "status": "ok",
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        **normalized,
    }


async def _request_and_cache(
    connection: dict[str, Any],
    definition: ProviderTelemetryDefinition,
    key: str,
) -> dict[str, Any]:
    result = await _request_provider(connection, definition)
    _CACHE[key] = (time.monotonic(), result)
    return result


def _schedule_refresh(
    connection: dict[str, Any],
    definition: ProviderTelemetryDefinition,
    key: str,
) -> asyncio.Task[dict[str, Any]]:
    existing = _REFRESH_TASKS.get(key)
    if existing is not None and not existing.done():
        return existing
    task = asyncio.create_task(_request_and_cache(connection, definition, key))
    _REFRESH_TASKS[key] = task

    def settled(done: asyncio.Task[dict[str, Any]]) -> None:
        if _REFRESH_TASKS.get(key) is done:
            _REFRESH_TASKS.pop(key, None)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            # Preserve the last usable value and stop clients from polling a
            # failed provider continuously. A later manual refresh can retry.
            cached = _CACHE.get(key)
            if cached is not None:
                stale = {**cached[1], "refresh_error": str(exc)}
                _CACHE[key] = (time.monotonic(), stale)

    task.add_done_callback(settled)
    return task


def _cache_key(connection: dict[str, Any], provider: str) -> str:
    secret = str(connection.get("api_key") or "")
    fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]
    return "\n".join((
        provider,
        str(connection.get("id") or ""),
        str(connection.get("base_url") or ""),
        fingerprint,
    ))


async def provider_telemetry(
    connection: dict[str, Any], *, force_refresh: bool = False
) -> dict[str, Any]:
    preset = _provider_preset(connection)
    definition = _DEFINITIONS.get(preset)
    if definition is None:
        raise ProviderTelemetryError("Provider does not expose account usage")
    key = _cache_key(connection, preset)
    cached = _CACHE.get(key)
    if cached is not None:
        age = time.monotonic() - cached[0]
        refreshing = False
        if force_refresh or age > _CACHE_TTL_SECONDS:
            refreshing = not _schedule_refresh(
                connection, definition, key
            ).done()
        return {**cached[1], "refreshing": refreshing}
    result = await _request_and_cache(connection, definition, key)
    return {**result, "refreshing": False}


async def configured_provider_telemetry(
    *, force_refresh: bool = False
) -> list[dict[str, Any]]:
    from cyrene.core.plugin import application_plugin_service

    configuration_service = application_plugin_service("model_configuration")
    if configuration_service is None:
        return []
    connections = configuration_service.get_model_configuration().get("connections") or []
    selected = [
        connection
        for connection in connections
        if (
            isinstance(connection, dict)
            and _provider_preset(connection) in _DEFINITIONS
            and str(connection.get("api_key") or "").strip()
        )
    ]

    async def fetch(connection: dict[str, Any]) -> dict[str, Any]:
        preset = _provider_preset(connection)
        definition = _DEFINITIONS[preset]
        try:
            return await provider_telemetry(connection, force_refresh=force_refresh)
        except (ProviderTelemetryError, httpx.HTTPError, ValueError) as exc:
            return {
                "connection_id": connection.get("id"),
                "provider": preset,
                "label": definition.label,
                "kind": "balance" if preset == "deepseek" else "quota",
                "status": "error",
                "available": False,
                "balances": [],
                "windows": [],
                "error": str(exc),
            }

    return list(await asyncio.gather(*(fetch(connection) for connection in selected)))


__all__ = [
    "ProviderTelemetryError",
    "configured_provider_telemetry",
    "provider_telemetry",
]
