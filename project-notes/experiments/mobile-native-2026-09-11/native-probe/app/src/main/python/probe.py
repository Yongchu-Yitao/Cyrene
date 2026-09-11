"""Real native dependencies + ASGI streaming + SQLite roundtrip, no LLM call."""
import importlib
import json
import time


def run(directory):
    result = {"imports_ms": {}, "unavailable": {}}
    for name in ("sqlite3", "ssl", "pydantic", "jsonschema", "httpx", "fastapi", "aiosqlite", "cyrene.core"):
        start = time.perf_counter()
        try:
            importlib.import_module(name)
            result["imports_ms"][name] = (time.perf_counter() - start) * 1000
        except ImportError as error:
            result["unavailable"][name] = str(error)
    import asyncio
    import sqlite3
    import sys
    from pathlib import Path
    import httpx

    result["python"] = sys.version
    path = str(Path(directory) / "probe.sqlite3")
    start = time.perf_counter()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, value TEXT)")
        for i in range(100):
            db.execute("INSERT INTO events(value) VALUES (?)", (json.dumps({"event": i}),))
            db.commit()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        result["event_count"] = db.execute("SELECT count(*) FROM events").fetchone()[0]
    result["sqlite_100_commits_ms"] = (time.perf_counter() - start) * 1000
    result["scope"] = "native interpreter + dependency probe; NOT full Cyrene"
    if "fastapi" in result["unavailable"]:
        result["asgi"] = "not tested: dependency unavailable"
        return json.dumps(result)
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    app = FastAPI()

    @app.get("/events")
    async def events():
        async def stream():
            for i in range(10):
                yield f"data: {i}\n\n"
                await asyncio.sleep(0)
        return StreamingResponse(stream(), media_type="text/event-stream")

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://probe") as client:
            response = await client.get("/events")
            assert response.status_code == 200 and response.text.count("data:") == 10
    start = time.perf_counter()
    asyncio.run(request())
    result["asgi_10_events_ms"] = (time.perf_counter() - start) * 1000
    result["scope"] = "dependency and in-process ASGI feasibility; NOT full Cyrene or wire streaming"
    return json.dumps(result)
