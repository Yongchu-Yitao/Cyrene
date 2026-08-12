"""
Tests for MCP manager: config persistence and tool integration.
"""

import sys
import tempfile
import asyncio
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture(autouse=True)
def _isolated_mcp_settings(monkeypatch):
    from cyrene.runtime import settings_store

    state = {"mcp_servers": None}
    monkeypatch.setattr(settings_store, "get", lambda key, default=None: state.get(key, default))
    monkeypatch.setattr(settings_store, "set_", lambda key, value: state.__setitem__(key, value))

def test_config_persistence_empty():
    """Default config should return an empty server list."""
    from cyrene.tooling.backends import mcp_manager as mm

    with tempfile.TemporaryDirectory() as tmp:
        mm._MCP_SERVERS_FILE = Path(tmp) / "mcp_servers.json"
        servers = mm.get_mcp_servers()
        assert servers == [], f"Expected empty list, got {servers}"


def test_config_persistence_save_and_load():
    """Save then load should return the same data."""
    from cyrene.tooling.backends import mcp_manager as mm

    with tempfile.TemporaryDirectory() as tmp:
        mm._MCP_SERVERS_FILE = Path(tmp) / "mcp_servers.json"
        wrapper = Path(tmp) / "test-mcp-server"
        wrapper.write_text("test executable", encoding="utf-8")
        test_servers = [
            {
                "name": "test-fs",
                "transport": "stdio",
                "command": str(wrapper),
                "args": ["-m", "test_mcp_server"],
                "enabled": True,
            },
            {
                "name": "test-sse",
                "transport": "sse",
                "url": "http://localhost:3000/mcp",
                "enabled": False,
            },
        ]
        mm.save_mcp_servers(test_servers)
        loaded = mm.get_mcp_servers()
        assert loaded == test_servers, f"Mismatch: {loaded} != {test_servers}"


def test_mcp_browser_payload_is_redacted_and_round_trips_sentinels():
    from cyrene.tooling.backends import mcp_manager as mm

    existing = [{
        "name": "secured", "transport": "streamable_http", "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer secret"},
    }]
    safe = mm.redact_mcp_servers(existing)
    assert "secret" not in str(safe)
    merged = mm.merge_redacted_mcp_servers(existing, safe)
    assert merged[0]["headers"]["Authorization"] == "Bearer secret"


def test_config_persistence_corrupted_file():
    """A corrupted JSON file should fall back to the default empty list."""
    from cyrene.tooling.backends import mcp_manager as mm

    with tempfile.TemporaryDirectory() as tmp:
        mcp_file = Path(tmp) / "mcp_servers.json"
        mm._MCP_SERVERS_FILE = mcp_file
        mcp_file.write_text("{{{ corrupted json", encoding="utf-8")
        servers = mm.get_mcp_servers()
        assert servers == [], f"Expected empty list fallback, got {servers}"


def test_config_rejects_dynamic_runners_and_insecure_remote_urls():
    from cyrene.tooling.backends import mcp_manager as mm

    with pytest.raises(ValueError, match="not allowed"):
        mm.save_mcp_servers([{"name": "dynamic", "transport": "stdio", "command": "npx", "args": ["-y", "pkg@latest"]}])
    with pytest.raises(ValueError, match="HTTPS"):
        mm.save_mcp_servers([{"name": "remote", "transport": "streamable_http", "url": "http://example.com/mcp"}])


def test_streamable_http_is_a_supported_transport(monkeypatch):
    from cyrene.tooling.backends.mcp_manager import MCPServerConnection

    connection = MCPServerConnection("remote", "streamable-http", {"url": "https://example.com/mcp"})
    assert connection.transport == "streamable-http"


def test_singleton_get_manager():
    """get_manager() should always return the same instance."""
    from cyrene.tooling.backends.mcp_manager import get_manager

    m1 = get_manager()
    m2 = get_manager()
    assert m1 is m2, "get_manager() returned different instances"


def test_get_tool_defs_with_no_servers():
    """With no connected servers, get_tool_defs() should return empty list."""
    from cyrene.tooling.backends.mcp_manager import get_manager

    manager = get_manager()
    defs = manager.get_tool_defs()
    assert defs == [], f"Expected empty tool defs, got {defs}"


def test_get_server_status_with_no_config():
    """With no config file, get_server_status() should return empty list."""
    from cyrene.tooling.backends.mcp_manager import get_manager

    manager = get_manager()
    status = manager.get_server_status()
    assert status == [], f"Expected empty status, got {status}"


def test_stdio_tool_timeout_defaults_to_and_is_capped_at_120_seconds():
    from cyrene.tooling.backends.mcp_manager import MCPServerConnection

    default = MCPServerConnection("default", "stdio", {})
    configured_lower = MCPServerConnection(
        "lower", "stdio", {"timeout_seconds": 45}
    )
    configured_higher = MCPServerConnection(
        "higher", "stdio", {"timeout_seconds": 600}
    )

    assert default.tool_timeout_seconds() == 120.0
    assert configured_lower.tool_timeout_seconds() == 45.0
    assert configured_higher.tool_timeout_seconds() == 120.0


def test_mcp_tool_def_conversion():
    """Verify the MCP Tool → Cyrene tool def format conversion."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

    # Simulate what MCPServerConnection._refresh_tools() does
    from mcp.types import Tool

    tool = Tool(
        name="read_file",
        description="Read a file from the filesystem",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"}
            },
            "required": ["path"],
        },
    )

    converted = {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
        },
    }

    assert converted["function"]["name"] == "read_file"
    assert converted["function"]["description"] == "Read a file from the filesystem"
    assert "path" in converted["function"]["parameters"]["properties"]
    assert converted["function"]["parameters"]["required"] == ["path"]


def test_get_active_tool_defs_includes_mcp():
    """get_active_tool_defs() should include MCP tools when manager has them."""
    from cyrene.tooling import catalog as tools
    from cyrene.tooling.backends import mcp_manager as mm

    with tempfile.TemporaryDirectory() as tmp:
        # Simulate a manager with tools
        mm._MCP_SERVERS_FILE = Path(tmp) / "mcp_servers.json"
        mm.save_mcp_servers([])

        # Test that get_active_tool_defs still works (includes native tools)
        defs = tools.get_active_tool_defs()
        names = [d["function"]["name"] for d in defs]
        assert "Read" in names, "Native tool 'Read' should be in active defs"
        assert "Bash" in names, "Native tool 'Bash' should be in active defs"
        assert len(defs) >= 20, f"Expected at least 20 tools, got {len(defs)}"


def test_execute_tool_unknown_fallback_to_mcp():
    """_execute_tool should try MCP for unknown tool names and raise ValueError if not found."""
    from cyrene.tooling.executor import _execute_tool
    import asyncio

    # Calling a non-existent tool should raise ValueError (not crash)
    async def run():
        try:
            await _execute_tool("nonexistent_mcp_tool_name_xyz", {}, None, 0, "", None)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Unknown tool" in str(e), f"Unexpected error: {e}"

    asyncio.run(run())


def test_start_stop_with_no_servers():
    """start_mcp() and stop_mcp() should work with empty config."""
    from cyrene.tooling.backends.mcp_manager import get_manager, start_mcp, stop_mcp
    import asyncio

    with tempfile.TemporaryDirectory() as tmp:
        from cyrene.tooling.backends import mcp_manager as mm
        mm._MCP_SERVERS_FILE = Path(tmp) / "mcp_servers.json"
        mm.save_mcp_servers([])

        asyncio.run(start_mcp())
        manager = get_manager()
        assert len(manager._servers) == 0, "No servers should be connected with empty config"
        stop_mcp()


@pytest.mark.asyncio
async def test_manager_cancellation_disconnects_staged_connection(monkeypatch):
    from cyrene.tooling.backends import mcp_manager as mm

    started = __import__("asyncio").Event()
    disconnected = __import__("asyncio").Event()

    class SlowConnection:
        def __init__(self, name, transport, config):
            self.name = name

        async def connect(self):
            started.set()
            await __import__("asyncio").Future()

        async def disconnect(self):
            disconnected.set()

    monkeypatch.setattr(mm, "get_mcp_servers", lambda: [{"name": "slow", "transport": "stdio", "enabled": True}])
    monkeypatch.setattr(mm, "MCPServerConnection", SlowConnection)
    manager = mm.MCPManager()
    task = __import__("asyncio").create_task(manager.start())
    await started.wait()
    task.cancel()
    with pytest.raises(__import__("asyncio").CancelledError):
        await task

    assert disconnected.is_set()
    assert manager._servers == {}


@pytest.mark.asyncio
async def test_cancelled_real_stdio_handshake_terminates_child_process(tmp_path, monkeypatch):
    from cyrene.tooling.backends import mcp_manager as mm

    pid_file = tmp_path / "server.pid"
    server = tmp_path / "slow-mcp-server"
    server.write_text(
        "#!/bin/sh\n"
        f"echo $$ > {pid_file}\n"
        "exec sleep 30\n",
        encoding="utf-8",
    )
    server.chmod(0o755)
    monkeypatch.setattr(mm, "get_mcp_servers", lambda: [{
        "name": "slow",
        "transport": "stdio",
        "command": str(server),
        "enabled": True,
        "startup_timeout_seconds": 60,
    }])

    manager = mm.MCPManager()
    task = asyncio.create_task(manager.start())
    pid = None
    try:
        deadline = asyncio.get_running_loop().time() + 5.0
        while not pid_file.is_file() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        assert pid_file.is_file()
        pid = int(pid_file.read_text(encoding="utf-8"))
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert pid is not None
    deadline = asyncio.get_running_loop().time() + 5.0
    while asyncio.get_running_loop().time() < deadline:
        try:
            __import__("os").kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail(f"cancelled MCP subprocess {pid} is still running")
    assert manager._servers == {}


@pytest.mark.asyncio
async def test_async_stop_uses_the_running_application_loop(monkeypatch):
    from cyrene.tooling.backends import mcp_manager as mm

    events = []

    class FakeManager:
        async def stop(self):
            events.append("stopped")

    monkeypatch.setattr(mm, "_manager", FakeManager())

    await mm.stop_mcp_async()

    assert events == ["stopped"]
    assert mm._manager is None
