"""Model-independent, multi-round benchmark for a live PowerPoint session.

The benchmark calls the installed PowerPoint Plugins through PluginRuntime. It
performs no LLM or search requests and keeps one Office session/revision chain alive
across all rounds::

    uv run python -m cyrene.observability.powerpoint_performance_benchmark \
      --rounds 5 --strategies stage,element \
      --json output/performance/powerpoint-live.json
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import secrets
import statistics
import time
from typing import Any, Awaitable, Callable


BenchmarkCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

_BENCHMARK_PLUGINS = {
    "ppt.get_context": "PowerPointGetContext",
    "ppt.create_slide": "PowerPointCreateSlide",
    "ppt.list_shapes": "PowerPointListShapes",
    "ppt.apply_batch": "PowerPointApplyBatch",
    "ppt.read_text": "PowerPointReadText",
    "ppt.delete_slide": "PowerPointDeleteSlide",
}
_LOCAL_PLUGIN_RUNTIME: tuple[Any, Any] | None = None


def _office_load_error(failures: Any) -> str:
    return "; ".join(
        str(item.error)
        for item in failures
        if getattr(getattr(item, "path", None), "name", "") == "cyrene_office"
    )


@dataclass(frozen=True, slots=True)
class PowerPointBenchmarkConfig:
    rounds: int = 5
    strategies: tuple[str, ...] = ("stage", "element")
    session_id: str = ""
    transport: str = "auto"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "min_ms": round(min(values), 3) if values else 0.0,
        "p50_ms": round(statistics.median(values), 3) if values else 0.0,
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


def _benchmark_spec(round_index: int) -> dict[str, Any]:
    return {
        "layout": "section-grid",
        "title": f"Cyrene PowerPoint benchmark {round_index + 1}",
        "subtitle": "Deterministic semantic composition",
        "sections": [
            {"heading": "Plan", "body": "Compact content contract"},
            {"heading": "Compile", "body": "Server-owned geometry"},
            {"heading": "Commit", "body": "Dependency-safe stages"},
            {"heading": "Verify", "body": "Stable refs and revision"},
        ],
        "footer": "No model calls",
        "theme": {
            "background": "#F7F5F2",
            "foreground": "#1F2937",
            "accent": "#C2410C",
            "muted": "#64748B",
            "fontFamily": "Aptos",
        },
    }


async def _default_caller(method: str, args: dict[str, Any]) -> dict[str, Any]:
    global _LOCAL_PLUGIN_RUNTIME
    from cyrene.core.plugin import (
        PluginActivationState,
        PluginContext,
        PluginRegistry,
        PluginRuntime,
        default_plugin_impl_directory,
    )
    from cyrene.plugins.native_tools import seed_builtin_plugin_directory
    from cyrene.runtime import settings_store

    plugin_name = _BENCHMARK_PLUGINS.get(method)
    if plugin_name is None:
        raise RuntimeError(f"unsupported PowerPoint benchmark method: {method}")
    if _LOCAL_PLUGIN_RUNTIME is None:
        directory = default_plugin_impl_directory()
        seed_builtin_plugin_directory(directory)
        registry = PluginRegistry(activation=PluginActivationState())
        failures = registry.load_directory(directory)
        office_error = _office_load_error(failures)
        if office_error:
            raise RuntimeError("PowerPoint Plugin failed to load: " + office_error)
        registry.configure_activation(
            plugins=settings_store.get_enabled_plugins(),
            packs=settings_store.get_enabled_plugin_packs(),
        )
        _LOCAL_PLUGIN_RUNTIME = registry, PluginRuntime(registry)
    registry, runtime = _LOCAL_PLUGIN_RUNTIME
    failures = registry.refresh()
    office_error = _office_load_error(failures)
    if office_error:
        raise RuntimeError("PowerPoint Plugin failed to refresh: " + office_error)
    call = await runtime.call_canonical(
        plugin_name,
        args,
        PluginContext(workspace=Path.cwd(), data={"source": "powerpoint_benchmark"}),
    )
    if not call.success:
        raise RuntimeError(str(call.error or f"{plugin_name} failed"))
    payload = call.value
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"PowerPoint returned invalid JSON for {method}: {payload[:240]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"PowerPoint returned a non-object result for {method}")
    return payload


class _GatewayCaller:
    def __init__(self) -> None:
        import httpx

        from cyrene.plugins.builtin.cyrene_office.gateway import OfficeGatewayFiles

        material = OfficeGatewayFiles()
        material.ensure()
        self._token = material.secret
        self._base_url = material.base_url
        self._client = httpx.AsyncClient(verify=False, timeout=300, trust_env=False)

    async def health(self) -> dict[str, Any]:
        response = await self._client.get(f"{self._base_url}/health")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def __call__(self, method: str, args: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(
            f"{self._base_url}/benchmark/invoke",
            params={"token": self._token},
            json={"method": method, "arguments": args},
        )
        if response.status_code == 404:
            raise RuntimeError("The running Cyrene backend predates the PowerPoint benchmark endpoint; restart the backend and reload the PowerPoint task pane.")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("PowerPoint benchmark gateway returned a non-object result")
        return payload

    async def close(self) -> None:
        await self._client.aclose()


async def _resolve_default_caller(config: PowerPointBenchmarkConfig) -> tuple[BenchmarkCaller, _GatewayCaller | None, str]:
    transport = str(config.transport or "auto").strip().lower()
    if transport not in {"auto", "gateway", "local"}:
        raise ValueError(f"unsupported PowerPoint benchmark transport: {transport}")
    if transport in {"auto", "gateway"}:
        gateway = _GatewayCaller()
        try:
            health = await gateway.health()
            if int(health.get("sessions") or 0) > 0:
                return gateway, gateway, "gateway"
            if transport == "gateway":
                raise RuntimeError("The Cyrene Office gateway has no connected PowerPoint session.")
        except Exception:
            await gateway.close()
            if transport == "gateway":
                raise
    return _default_caller, None, "local"


def _raise_tool_error(method: str, payload: dict[str, Any]) -> None:
    if payload.get("status") == "error":
        raise RuntimeError(
            f"{method} failed [{payload.get('error_code') or 'office_error'}]: "
            f"{payload.get('message') or payload}"
        )


def _created_slide_id(payload: dict[str, Any]) -> str:
    if payload.get("slideId"):
        return str(payload["slideId"])
    inserted = payload.get("insertedSlideIds") or []
    if inserted:
        return str(inserted[-1])
    for item in reversed(payload.get("created") or []):
        if isinstance(item, dict) and item.get("slideId"):
            return str(item["slideId"])
    return ""


async def _timed_call(
    caller: BenchmarkCaller,
    method: str,
    args: dict[str, Any],
) -> tuple[dict[str, Any], float, int]:
    payload_bytes = len(json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    started = time.perf_counter()
    result = await caller(method, args)
    latency_ms = (time.perf_counter() - started) * 1000
    _raise_tool_error(method, result)
    return result, latency_ms, payload_bytes


async def _run_strategy(
    config: PowerPointBenchmarkConfig,
    strategy: str,
    caller: BenchmarkCaller,
    run_key: str,
) -> dict[str, Any]:
    if strategy not in {"stage", "element"}:
        raise ValueError(f"unsupported PowerPoint benchmark strategy: {strategy}")
    granularity = "element" if strategy == "element" else "stage"
    base = {"sessionId": config.session_id} if config.session_id else {}
    step_latencies: dict[str, list[float]] = {
        "context": [], "create": [], "inspect": [], "edit": [], "verify": [], "delete": [],
    }
    round_wall: list[float] = []
    payload_bytes = 0
    completed = 0
    final_revision = 0
    failures: list[dict[str, Any]] = []

    for round_index in range(max(2, int(config.rounds))):
        round_started = time.perf_counter()
        slide_id = ""
        revision: int | None = None
        key = f"ppt-benchmark:{run_key}:{strategy}:{round_index + 1}"
        try:
            context, latency, size = await _timed_call(caller, "ppt.get_context", dict(base))
            step_latencies["context"].append(latency)
            payload_bytes += size
            revision = int(context.get("revision") or 0)

            created, latency, size = await _timed_call(caller, "ppt.create_slide", {
                **base,
                "expectedRevision": revision,
                "idempotencyKey": f"{key}:create",
                "progressiveGranularity": granularity,
                "slideSpec": _benchmark_spec(round_index),
            })
            step_latencies["create"].append(latency)
            payload_bytes += size
            slide_id = _created_slide_id(created)
            if not slide_id:
                raise RuntimeError("ppt.create_slide did not return a slide ID")
            revision = int(created.get("revision") or revision)

            inspected, latency, size = await _timed_call(caller, "ppt.list_shapes", {
                **base, "slideId": slide_id, "includeText": True,
            })
            step_latencies["inspect"].append(latency)
            payload_bytes += size
            refs = {str(item.get("ref") or "") for item in (inspected.get("shapes") or [])}
            if "title" not in refs:
                raise RuntimeError("compiled slide did not expose the stable title ref")

            updated_title = f"Cyrene PowerPoint benchmark {round_index + 1} complete"
            edited, latency, size = await _timed_call(caller, "ppt.apply_batch", {
                **base,
                "slideId": slide_id,
                "expectedRevision": revision,
                "idempotencyKey": f"{key}:edit",
                "progressiveGranularity": "stage",
                "operations": [{"op": "update_text", "shapeRef": "title", "text": updated_title}],
            })
            step_latencies["edit"].append(latency)
            payload_bytes += size
            revision = int(edited.get("revision") or revision)

            verified, latency, size = await _timed_call(caller, "ppt.read_text", {
                **base, "slideId": slide_id,
            })
            step_latencies["verify"].append(latency)
            payload_bytes += size
            if updated_title not in {str(item.get("text") or "") for item in (verified.get("text") or [])}:
                raise RuntimeError("updated title was not observable after the edit")
            completed += 1
        except Exception as exc:
            failures.append({"round": round_index + 1, "message": str(exc)})
        finally:
            if slide_id and revision is not None:
                try:
                    deleted, latency, size = await _timed_call(caller, "ppt.delete_slide", {
                        **base,
                        "slideId": slide_id,
                        "expectedRevision": revision,
                        "idempotencyKey": f"{key}:delete",
                    })
                    step_latencies["delete"].append(latency)
                    payload_bytes += size
                    final_revision = int(deleted.get("revision") or revision)
                except Exception as exc:
                    failures.append({"round": round_index + 1, "phase": "cleanup", "message": str(exc)})
            round_wall.append((time.perf_counter() - round_started) * 1000)

    return {
        "scenario": strategy,
        "composition_strategy": "powerpoint_addin",
        "progressive_granularity": granularity,
        "rounds": max(2, int(config.rounds)),
        "completed_rounds": completed,
        "wall_ms": round(sum(round_wall), 3),
        "round_latency": _latency_summary(round_wall),
        "steps": {name: _latency_summary(values) for name, values in step_latencies.items()},
        "request_payload_bytes": payload_bytes,
        "final_revision": final_revision,
        "quality": {
            "preserved": completed == max(2, int(config.rounds)) and not failures,
            "failures": failures,
        },
    }


async def run_benchmark(
    *,
    config: PowerPointBenchmarkConfig = PowerPointBenchmarkConfig(),
    caller: BenchmarkCaller | None = None,
) -> dict[str, Any]:
    selected = tuple(dict.fromkeys(config.strategies))
    if not selected:
        raise ValueError("at least one benchmark strategy is required")
    run_key = secrets.token_hex(6)
    managed_gateway: _GatewayCaller | None = None
    transport = "injected"
    if caller is None:
        invoke, managed_gateway, transport = await _resolve_default_caller(config)
    else:
        invoke = caller
    try:
        results = [await _run_strategy(config, strategy, invoke, run_key) for strategy in selected]
    finally:
        if managed_gateway is not None:
            await managed_gateway.close()
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "real_llm_calls": False,
            "network_search": False,
            "live_powerpoint": caller is None,
            "continuous_session": True,
            "transport": transport,
        },
        "config": asdict(config),
        "results": results,
        "quality": {
            "preserved": all(item["quality"]["preserved"] for item in results),
            "failed_scenarios": [item["scenario"] for item in results if not item["quality"]["preserved"]],
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Cyrene live PowerPoint benchmark",
        "",
        "This benchmark performs no model calls. All rounds reuse one connected PowerPoint session.",
        "",
        "| Strategy | Rounds | Completed | Total | Round P50 | Round P95 | Create P50 | Edit P50 | Quality |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["results"]:
        lines.append(
            "| {scenario} | {rounds} | {completed_rounds} | {wall_ms:.3f} ms | "
            "{round_p50:.3f} ms | {round_p95:.3f} ms | {create_p50:.3f} ms | "
            "{edit_p50:.3f} ms | {quality_label} |".format(
                **item,
                round_p50=item["round_latency"]["p50_ms"],
                round_p95=item["round_latency"]["p95_ms"],
                create_p50=item["steps"]["create"]["p50_ms"],
                edit_p50=item["steps"]["edit"]["p50_ms"],
                quality_label="pass" if item["quality"]["preserved"] else "FAIL",
            )
        )
    lines.append("")
    return "\n".join(lines)


async def write_report(path: Path, *, config: PowerPointBenchmarkConfig) -> tuple[Path, Path, dict[str, Any]]:
    report = await run_benchmark(config=config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = path.with_suffix(".md")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return path, markdown_path, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--strategies", default="stage,element")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--transport", choices=("auto", "gateway", "local"), default="auto")
    parser.add_argument("--json", type=Path, default=Path("output/performance/powerpoint-live.json"))
    parser.add_argument("--fail-on-quality", action="store_true")
    args = parser.parse_args()
    config = PowerPointBenchmarkConfig(
        rounds=max(2, args.rounds),
        strategies=tuple(value.strip() for value in args.strategies.split(",") if value.strip()),
        session_id=args.session_id,
        transport=args.transport,
    )
    json_path, markdown_path, report = asyncio.run(write_report(args.json, config=config))
    print(json_path)
    print(markdown_path)
    if args.fail_on_quality and not report["quality"]["preserved"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()


__all__ = ["PowerPointBenchmarkConfig", "render_markdown", "run_benchmark", "write_report"]
