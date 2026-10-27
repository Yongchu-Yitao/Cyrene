"""Exercise production Search/Fetch and AgentSession with isolated local credentials.

Run with ``uv run python project-notes/experiments/deepseek-search-2026-10-27/probe.py --output /absolute/report.json``.
The real encrypted configuration is copied into a temporary runtime; only safe
HTTP metadata, tool observations and the synthetic agent's answer are exported.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def experiment(output: Path) -> int:
    import httpx
    from fastapi import APIRouter, FastAPI
    from cyrene.core.plugin import PluginContext, PluginRegistry
    from cyrene.core.session import AgentSession
    from cyrene.platform import config_store, database
    from cyrene.plugins.application import PluginApplicationHost, set_application_plugin_scope
    from cyrene.plugins.builtin import cyrene_content, cyrene_model
    from cyrene.plugins.builtin.cyrene_content import deepseek_web_search as dws
    from cyrene.plugins.builtin.cyrene_model.configuration import get_model_configuration, candidate_for_profile

    logging.basicConfig(level=logging.ERROR)
    root = config_store.DATA_DIR.parent
    report: dict = {
        "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "cases": [], "http": [], "agent_events": [],
    }
    secret = ""
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        serialized = json.dumps(report, ensure_ascii=False, indent=2)
        if secret:
            serialized = serialized.replace(secret, "[redacted]")
        output.write_text(serialized + "\n")

    original_send = httpx.AsyncClient.send

    async def observed_send(client, request, **kwargs):
        started = time.monotonic()
        record = {"method": request.method, "url": str(request.url)}
        if request.method == "POST" and request.url.host == "api.deepseek.com":
            payload = json.loads(request.content)
            record["parameters"] = {key: payload[key] for key in ("query", "count", "need_content", "url", "model") if key in payload}
            record["tool_names"] = [item.get("function", {}).get("name", item.get("type")) for item in payload.get("tools", [])]
        try:
            response = await original_send(client, request, **kwargs)
            record["status"] = response.status_code
            if not kwargs.get("stream") and request.url.path == "/v1/tools/search" and response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                record["results"] = [{"url": item.get("url"), "summary_chars": len(item.get("summary") or ""), "content_chars": len(item.get("content") or "")} for item in results]
            return response
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["seconds"] = round(time.monotonic() - started, 3)
            report["http"].append(record)

    httpx.AsyncClient.send = observed_send
    app = FastAPI()
    registry = PluginRegistry()
    for module in (cyrene_model, cyrene_content):
        registry.register_pack(module.plugin_pack, source=str(Path(module.__file__).parent))
    plugins = root / "plugin_impl"
    plugins.mkdir(exist_ok=True)
    db_path = str(root / "live.db")
    await database.init_db(db_path)
    host = PluginApplicationHost(app=app, registry=registry, bot=None, db_path=db_path, data_directory=config_store.DATA_DIR, plugin_directory=plugins)
    set_application_plugin_scope(host)
    router = APIRouter()
    host.attach(router)
    app.include_router(router)
    assert not host.setup_failures, host.setup_failures
    config = get_model_configuration(persist_seed=False)
    candidates = [candidate_for_profile(p["id"], config) for p in config["profiles"]]
    selected = dws.find_official_deepseek_search_candidate([c for c in candidates if c is not None])
    assert selected is not None, "No enabled official DeepSeek profile with credentials"
    secret = selected.api_key
    report["model"] = selected.configured_model
    saved_search = config_store.get_setting("search", {})
    config_store.set_setting("search", {**saved_search, "provider_order": ["deepseek", "simplexng", "tavily", "brave"], "provider_enabled": {"deepseek": True, "simplexng": False, "tavily": False, "brave": False}})
    await host.startup()
    assert not host.startup_failures, host.startup_failures
    context = PluginContext(data={"db_path": db_path, "run_id": "deepseek-live-tools"}, services=host.active_services)

    async def tool(name: str, arguments: dict) -> str:
        result = await host.runtime.call(name, arguments, context)
        assert result.success, f"{name}: {result.error}"
        assert isinstance(result.value, str) and result.value.strip()
        return result.value

    async def case(name, run):
        started = time.monotonic()
        first_http = len(report["http"])
        try:
            details = await asyncio.wait_for(run(), timeout=180)
            record = {"name": name, "success": True, **details}
        except Exception as exc:
            message = str(exc).replace(selected.api_key, "[redacted]")
            record = {"name": name, "success": False, "error_type": type(exc).__name__, "error": message[:500]}
        record.update(seconds=round(time.monotonic() - started, 3), http_range=[first_http, len(report["http"])])
        report["cases"].append(record)
        save()
        print(json.dumps(record, ensure_ascii=False), flush=True)

    query = "DeepSeek Search Fetch API site:api-docs.deepseek.com"

    def observation(text):
        return {"characters": len(text), "sha256": hashlib.sha256(text.encode()).hexdigest(), "excerpt": text[:280]}

    async def search(detail):
        text = await tool("WebSearch", {"query": query, "detail": detail, "max_results": 3})
        assert "DeepSeek Search" in text and "URL: https://" in text
        last = report["http"][-1]
        assert last["url"] == "https://api.deepseek.com/v1/tools/search" and last["status"] == 200
        assert last["parameters"]["need_content"] == (detail == "content")
        return observation(text)

    async def fetch():
        start = len(report["http"])
        text = await tool("WebFetch", {"url": "https://api-docs.deepseek.com/guides/search/"})
        calls = report["http"][start:]
        assert any(c["url"] == "https://api.deepseek.com/v1/tools/fetch" and c.get("status") == 200 for c in calls), "Fetch did not succeed via DeepSeek API"
        assert "need_content" in text.replace("\\", "") and "Search" in text, "Fetch content is missing the expected API documentation"
        return observation(text)

    async def fetch_fallback():
        from copy import deepcopy
        broken = deepcopy(config)
        connection = next(c for c in broken["connections"] if any(p["id"] == selected.candidate_id and p["connection_id"] == c["id"] for p in broken["profiles"]))
        connection["api_key"] = "invalid-deepseek-live-experiment-key"
        config_store.set_setting("model_configuration", broken)
        try:
            start = len(report["http"])
            text = await tool("WebFetch", {"url": "https://example.com/"})
            calls = report["http"][start:]
            assert any(c["url"] == "https://api.deepseek.com/v1/tools/fetch" and c.get("status") == 401 for c in calls)
            assert any(c["url"] == "https://example.com/" and c.get("status") == 200 for c in calls)
            assert "Example Domain" in text
            return {**observation(text), "fault_injection": "Invalid key in temporary configuration only; real HTTP 401 then direct HTTP 200"}
        finally:
            config_store.set_setting("model_configuration", config)

    async def local_fetch():
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h1>Cyrene local Fetch verification</h1>")
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            start = len(report["http"])
            text = await tool("WebFetch", {"url": f"http://127.0.0.1:{server.server_port}/"})
            assert "Cyrene local Fetch verification" in text
            assert not any(c["url"].startswith("https://api.deepseek.com") for c in report["http"][start:])
            return observation(text)
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()

    async def agent():
        def event(item):
            if item.type in {"assistant.tool_calls", "tool.completed", "assistant.completed", "run.failed"}:
                data = dict(item.data)
                report["agent_events"].append({"type": item.type, "data": data})
                print(json.dumps({"agent_event": item.type}, ensure_ascii=False), flush=True)
        session = AgentSession(
            root / "session-data", root / "workspace", plugins,
            registry=registry, load_plugins=False, application_scope=host,
            model_plugin="CyreneModelRouter", max_model_calls=6,
            plugin_services=host.active_services,
            plugin_context_data={"db_path": db_path, "model_identity": {"profile_id": selected.candidate_id}},
            extra_direct_tool_names=("WebSearch", "WebFetch"),
            initial_root_value={"role": "system", "content": "You are running a live integration check. Follow the user's requested web tool sequence and give a brief factual answer with source links."},
            event_listener=event,
        )
        try:
            session.submit(
                '请依次执行：1. 用 WebSearch 搜索“DeepSeek Search Fetch API site:api-docs.deepseek.com”，detail="preview"，max_results=2。'
                '2. 再用 WebSearch 搜索相同内容，detail="content"，max_results=2。'
                '3. 用 WebFetch 读取 https://api-docs.deepseek.com/guides/search/ 。'
                '4. 根据真实工具结果，用中文简述 Search 和 Fetch 各自作用、请求路径及查询长度限制，并附来源链接。总回答控制在250字以内。',
                run_id="deepseek-live-agent",
            )
            await session.drain()
            final = session.final_output("deepseek-live-agent")
            assert final and final.get("content"), "Agent produced no final answer"
            completed = [e for e in report["agent_events"] if e["type"] == "tool.completed"]
            assert len(completed) >= 3 and all(e["data"].get("success") for e in completed), "Agent did not complete all requested tools successfully"
            assert [e["data"]["name"] for e in completed] == ["WebSearch", "WebSearch", "WebFetch"], "Unexpected tool sequence"
            text = final["content"]
            assert "/v1/tools/search" in text and "/v1/tools/fetch" in text and "128" in text
            return {"answer": text, "completed_tools": len(completed), "model": selected.configured_model}
        finally:
            session.close()

    try:
        await case("tool_search_preview", lambda: search("preview"))
        await case("tool_search_content", lambda: search("content"))
        await case("tool_fetch_markdown", fetch)
        await case("tool_fetch_real_401_fallback", fetch_fallback)
        await case("tool_fetch_loopback", local_fetch)
        await case("production_agent_session", agent)
    finally:
        await host.shutdown()
        set_application_plugin_scope(None)
        httpx.AsyncClient.send = original_send
    report["success"] = all(c["success"] for c in report["cases"])
    report["completed_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    # Keep exported events limited to the synthetic task and never export secrets.
    serialized = json.dumps(report, ensure_ascii=False, indent=2).replace(selected.api_key, "[redacted]")
    output.write_text(serialized + "\n")
    return 0 if report["success"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return asyncio.run(experiment(args.output.resolve()))
    from cyrene.platform import config_store
    source = config_store.DATA_DIR
    files = ("config.enc", ".config_key")
    before = {name: digest(source / name) for name in files}
    with tempfile.TemporaryDirectory(prefix="cyrene-deepseek-live-") as temp:
        data = Path(temp) / "data"
        data.mkdir(mode=0o700)
        for name in files:
            shutil.copy2(source / name, data / name)
            (data / name).chmod(0o600)
        env = {**os.environ, "CYRENE_BASE_DIR": temp, "SEARXNG_AUTO_START": "0"}
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", "--output", str(args.output.resolve())], env=env, check=False)
    unchanged = all(digest(source / name) == before[name] for name in files)
    if args.output.exists():
        report = json.loads(args.output.read_text())
        report["source_configuration_unchanged"] = unchanged
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return completed.returncode if unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
