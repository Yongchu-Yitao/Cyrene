"""Offline guest acceptance: filesystem/SQLite, Bash/Git/Node, MCP, Chromium."""
import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from playwright.async_api import async_playwright


async def main():
    started = time.monotonic()
    result = {"guest_arch": platform.machine(), "python": platform.python_version()}
    def stage(name):
        print(json.dumps({"stage": name, "elapsed_seconds": round(time.monotonic() - started, 2)}), flush=True)
    stage("files_and_sqlite")
    with tempfile.TemporaryDirectory(prefix="cyrene-probe-", dir="/workspace") as directory:
        root = Path(directory)
        (root / "proof.txt").write_text("cyrene-mobile")
        assert (root / "proof.txt").read_text() == "cyrene-mobile"
        with sqlite3.connect(root / "proof.sqlite3") as db:
            db.execute("CREATE TABLE proof (value TEXT)")
            db.execute("INSERT INTO proof VALUES (?)", ("durable",))
        with sqlite3.connect(root / "proof.sqlite3") as db:
            assert db.execute("SELECT value FROM proof").fetchone() == ("durable",)
        result["files_and_sqlite"] = True
        for name, command in {
            "bash": ["bash", "-c", "printf BASH_OK"],
            "git": ["git", "init", "--quiet", str(root / "repository")],
            "node": ["node", "-e", "process.stdout.write('NODE_OK')"],
            "uv": ["uv", "--version"],
            "codex": [str(importlib.metadata.distribution("openai-codex-cli-bin")
                          .locate_file("codex_cli_bin/bin/codex")), "--version"],
        }.items():
            stage(name)
            subprocess.run(command, check=True, timeout=30, capture_output=True)
            result[name] = True
        server = root / "mcp_probe.py"
        server.write_text(
            'try:\n    from mcp.server.mcpserver import MCPServer\n'
            'except ImportError:\n    from mcp.server.fastmcp import FastMCP as MCPServer\n'
            'mcp = MCPServer("cyrene-probe")\n'
            '@mcp.tool()\n'
            'def ping() -> str:\n    return "pong"\n'
            'mcp.run(transport="stdio")\n'
        )
        stage("mcp_stdio_tool")
        async with stdio_client(StdioServerParameters(command=sys.executable, args=[str(server)])) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                answer = await session.call_tool("ping", {})
                is_error = answer.is_error if hasattr(answer, "is_error") else answer.isError
                assert not is_error and answer.content[0].text == "pong"
                result["mcp_stdio_tool"] = True
        stage("chromium_render")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content("<title>Cyrene probe</title><button>Ready</button>")
                await page.get_by_role("button", name="Ready").click()
                assert await page.title() == "Cyrene probe"
                screenshot = await page.screenshot()
                assert screenshot.startswith(b"\x89PNG\r\n\x1a\n")
                result["chromium_render"] = hashlib.sha256(screenshot).hexdigest()
            finally:
                await browser.close()
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=180))
