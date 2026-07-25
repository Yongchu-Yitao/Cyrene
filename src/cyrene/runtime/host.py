"""Local CLI and source-mode Electron backend entry point."""

# Direct file execution must bootstrap the source checkout before Cyrene imports.
# ruff: noqa: E402

import asyncio
import logging
import os
import socket
import sys
import uuid
from pathlib import Path


def _bootstrap_source_checkout() -> None:
    """Make direct ``local_cli.py`` execution behave like an installed module.

    Electron development launches this file by path. In that mode Python only
    adds ``src/cyrene`` to ``sys.path``, so ``import cyrene`` would otherwise
    fail. Prefer the checkout's virtual environment when it exists, then add
    the repository's ``src`` directory before importing any Cyrene modules.
    """
    if __package__:
        return

    entrypoint = Path(__file__).resolve()
    src_dir = entrypoint.parents[1]
    project_dir = entrypoint.parents[2]
    src_text = str(src_dir)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)

    if os.environ.get("CYRENE_LOCAL_CLI_BOOTSTRAPPED") == "1":
        return

    venv_dir = project_dir / ".venv"
    venv_python = (
        venv_dir / "Scripts" / "python.exe"
        if os.name == "nt"
        else venv_dir / "bin" / "python3"
    )
    try:
        already_in_checkout_venv = (
            Path(sys.prefix).resolve() == venv_dir.resolve()
        )
    except OSError:
        already_in_checkout_venv = False
    if not venv_python.is_file() or already_in_checkout_venv:
        return

    child_env = os.environ.copy()
    child_env["CYRENE_LOCAL_CLI_BOOTSTRAPPED"] = "1"
    child_env["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (src_text, child_env.get("PYTHONPATH", ""))
        if part
    )
    os.execve(
        str(venv_python),
        [str(venv_python), str(entrypoint), *sys.argv[1:]],
        child_env,
    )


_bootstrap_source_checkout()

from cyrene.agent import clear_session_id, run_agent
from cyrene.agent.commands import DEEP_REFLECT_COMMAND_ID, parse_deep_reflect_command
from cyrene.config import (
    ASSISTANT_NAME,
    DATA_DIR,
    DB_PATH,
    INBOX_DIR,
    SEARXNG_AUTO_START,
    SEARXNG_HOST,
    SEARXNG_PORT,
    STORE_DIR,
    WEB_PORT,
    WORKSPACE_DIR,
)
from cyrene.runtime.application import ApplicationLifecycle
from cyrene.runtime.bootstrap import create_runtime_context
from cyrene.runtime.database import init_db
from cyrene.runtime.inbox import ensure_inbox
from cyrene.runtime.memory.short_term import init_short_term
from cyrene.runtime.memory.soul import ensure_soul

logger = logging.getLogger(__name__)


async def _shielded_application_shutdown(
    application: ApplicationLifecycle,
) -> None:
    """Finish owned cleanup even when the host task is being cancelled."""
    cleanup_task = asyncio.create_task(application.shutdown())
    try:
        await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        await cleanup_task
        raise


def _get_default_ui_mode() -> str:
    """Return the UI mode baked in at build time, defaulting to 'workbench'."""
    try:
        from cyrene.runtime.buildinfo import DEFAULT_UI_MODE
        return DEFAULT_UI_MODE
    except Exception:
        return "workbench"


def _pick_web_port(preferred_port: int = WEB_PORT) -> int:
    """Return the preferred port when free, otherwise choose an ephemeral port."""
    for candidate in (preferred_port, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return int(sock.getsockname()[1])
    raise RuntimeError("Failed to allocate a local web port")


def _read_int_flag(argv: list[str], name: str) -> int | None:
    """Read a simple integer CLI flag from argv.

    Supports both ``--port 4242`` and ``--port=4242``.
    """
    prefix = f"{name}="
    for idx, arg in enumerate(argv):
        if arg == name:
            if idx + 1 >= len(argv):
                raise SystemExit(f"{name} requires an integer value")
            raw = argv[idx + 1]
        elif arg.startswith(prefix):
            raw = arg[len(prefix):]
        else:
            continue
        try:
            return int(raw)
        except ValueError as exc:
            raise SystemExit(f"{name} requires an integer value, got {raw!r}") from exc
    return None


async def _prepare_application(
    application: ApplicationLifecycle | None = None,
) -> ApplicationLifecycle:
    """Initialize and return the lifecycle owned by a CLI host."""
    application = application or ApplicationLifecycle(
        create_runtime_context(host_mode="cli")
    )
    await application.initialize()
    await application.start_external_services()
    return application


async def _prepare_cli() -> None:
    """Historical setup hook retained with its original no-argument contract."""
    await _prepare_application()


# ---------------------------------------------------------------------------
# MCP CLI helpers (shared between menu and command-line flags)
# ---------------------------------------------------------------------------


async def _cli_mcp_list() -> None:
    from cyrene.tooling.backends.mcp_manager import get_manager as _get_mgr, get_mcp_servers as _get_cfg

    configs = _get_cfg()
    if not configs:
        print("  No MCP servers configured.")
        return
    manager = _get_mgr()
    statuses = {s["name"]: s for s in manager.get_server_status()}
    print(f"\n  {'Name':<16} {'Transport':<10} {'Status':<14} {'Tools':<6} Endpoint")
    print(f"  {'-'*16} {'-'*10} {'-'*14} {'-'*6} {'-'*40}")
    for cfg in configs:
        name = cfg.get("name", "?")
        st = statuses.get(name, {})
        status = st.get("status", "disconnected")
        tools = st.get("tool_count", 0)
        transport = cfg.get("transport", "stdio")
        endpoint = cfg.get("command", "") if transport == "stdio" else cfg.get("url", "")
        enabled = cfg.get("enabled", True)
        enabled_mark = "" if enabled else " [disabled]"
        print(f"  {name:<16} {transport:<10} {status:<14} {tools:<6} {endpoint}{enabled_mark}")
    # Show tool summary if any connected
    mcp_defs = manager.get_tool_defs()
    if mcp_defs:
        print(f"\n  Total MCP tools available: {len(mcp_defs)}")
        for td in mcp_defs:
            print(f"    - {td['function']['name']}: {td['function']['description'][:80]}")


async def _cli_mcp_add(args: list[str]) -> None:
    from cyrene.tooling.backends.mcp_manager import save_mcp_servers as _save, get_mcp_servers as _load

    if len(args) < 3:
        print("  Usage: add <name> stdio <command> [args...]")
        print("         add <name> sse <url>")
        return
    name, transport = args[0], args[1]
    if transport == "stdio":
        command = args[2]
        extra_args = args[3:]
        server = {"name": name, "transport": "stdio", "command": command, "args": extra_args, "enabled": True}
    elif transport == "sse":
        url = args[2]
        server = {"name": name, "transport": "sse", "url": url, "enabled": True}
    else:
        print(f"  Unknown transport: {transport} (use stdio or sse)")
        return
    servers = _load()
    servers = [s for s in servers if s.get("name") != name]
    servers.append(server)
    _save(servers)
    # Restart MCP manager
    from cyrene.tooling.backends.mcp_manager import restart_mcp

    await restart_mcp()
    print(f"  ✅ MCP server '{name}' added and connected.")


async def _cli_mcp_remove(args: list[str]) -> None:
    from cyrene.tooling.backends.mcp_manager import save_mcp_servers as _save, get_mcp_servers as _load, restart_mcp

    if not args:
        print("  Usage: remove <name>")
        return
    name = args[0]
    servers = _load()
    before = len(servers)
    servers = [s for s in servers if s.get("name") != name]
    if len(servers) == before:
        print(f"  Server '{name}' not found.")
        return
    _save(servers)
    await restart_mcp()
    print(f"  ✅ MCP server '{name}' removed.")


async def _cli_mcp_toggle(args: list[str]) -> None:
    from cyrene.tooling.backends.mcp_manager import save_mcp_servers as _save, get_mcp_servers as _load, restart_mcp

    if not args:
        print("  Usage: toggle <name>")
        return
    name = args[0]
    servers = _load()
    found = False
    for s in servers:
        if s.get("name") == name:
            s["enabled"] = not s.get("enabled", True)
            found = True
            break
    if not found:
        print(f"  Server '{name}' not found.")
        return
    _save(servers)
    await restart_mcp()
    status = "enabled" if next(s for s in servers if s["name"] == name).get("enabled", True) else "disabled"
    print(f"  ✅ MCP server '{name}' {status}.")


async def _cli_mcp_test(args: list[str]) -> None:
    from cyrene.tooling.backends.mcp_manager import get_manager as _get_mgr

    if not args:
        print("  Usage: test <name>")
        return
    name = args[0]
    manager = _get_mgr()
    for conn_name, conn in manager._servers.items():
        if conn_name == name:
            tools = conn.get_tool_defs()
            print(f"  ✅ Server '{name}' connected, {len(tools)} tools available.")
            for td in tools[:10]:
                print(f"    - {td['function']['name']}: {td['function']['description'][:60]}")
            if len(tools) > 10:
                print(f"    ... and {len(tools) - 10} more")
            return
    print(f"  Server '{name}' is not connected. Check config with '/mcp list'.")


async def _handle_mcp_command(cmd_line: str) -> None:
    parts = cmd_line.strip().split()
    if not parts:
        return
    sub = parts[0].lower()
    rest = parts[1:]
    if sub == "list":
        await _cli_mcp_list()
    elif sub == "add":
        await _cli_mcp_add(rest)
    elif sub == "remove":
        await _cli_mcp_remove(rest)
    elif sub == "toggle":
        await _cli_mcp_toggle(rest)
    elif sub == "test":
        await _cli_mcp_test(rest)
    else:
        print(f"  Unknown mcp command: {sub}")
        print("  Commands: list, add, remove, toggle, test")


def _show_help():
    print()
    print("=" * 40)
    print("  Cyrene 帮助菜单")
    print("=" * 40)
    print("  1) 重新注入人格（重新运行设置向导）")
    print("  2) 清除对话上下文（session）")
    print("  3) 重置人格（恢复默认 SOUL.md）")
    print("  4) 检查系统状态")
    print("  0) 返回对话")
    print("=" * 40)


async def _handle_menu():
    while True:
        choice = input("\n选择操作 (0-4): ").strip()

        if choice == "0":
            print("返回对话。")
            return

        elif choice == "1":
            from cyrene.runtime.setup import init_setup_flag, run_setup
            init_setup_flag()
            print("\n--- 重新注入人格 ---")
            await run_setup()
            print("人格设置完成。输入 /h 可以重新设置。")
            return

        elif choice == "2":
            await clear_session_id()
            print("✅ 对话上下文已清除。")
            return

        elif choice == "3":
            from cyrene.runtime.memory.soul import get_soul_path, ensure_soul
            from cyrene.runtime.memory.short_term import save_entries
            soul_path = get_soul_path()
            if soul_path.exists():
                soul_path.unlink()
            ensure_soul()
            save_entries([])  # 同时清空短期记忆
            print("✅ SOUL.md 已重置为默认。短期记忆已清空。")
            return

        elif choice == "4":
            from cyrene.config import OPENAI_MODEL, OPENAI_BASE_URL
            from cyrene.runtime.memory.soul import get_soul_path, read_soul
            from cyrene.runtime.memory.short_term import load_entries
            print("\n--- 系统状态 ---")
            print(f"  模型: {OPENAI_MODEL}")
            print(f"  地址: {OPENAI_BASE_URL}")
            soul_path = get_soul_path()
            print(f"  SOUL.md: {'存在' if soul_path.exists() else '不存在'} ({soul_path})")
            if soul_path.exists():
                soul_content = read_soul()
                print(f"  人格内容: {len(soul_content)} 字符")
            st_entries = load_entries()
            print(f"  短期记忆: {len(st_entries)} 条")
            from cyrene.config import STATE_FILE
            if STATE_FILE.exists():
                import json
                msgs = json.loads(STATE_FILE.read_text()).get("messages", [])
                print(f"  当前 session: {len(msgs)} 条消息")
            else:
                print("  当前 session: 空")
            # MCP 状态
            from cyrene.tooling.backends.mcp_manager import get_manager as _get_mgr, get_mcp_servers as _get_cfg
            mcp_cfgs = _get_cfg()
            if mcp_cfgs:
                print(f"  MCP 服务器: {len(mcp_cfgs)} 个已配置")
                mcp_mgr = _get_mgr()
                for st in mcp_mgr.get_server_status():
                    print(f"    {st['name']}: {st['status']} ({st['tool_count']} tools)")
            print("------------------")
            return

        else:
            print("无效选择，请输入 0-4。")


async def _cli_loop() -> None:
    print(f"{ASSISTANT_NAME} CLI mode. '/h' for menu, '/clear' to reset session, '/mcp' for MCP management, 'quit' to exit.")
    while True:
        try:
            user_input = input("\nYou: ").strip()
            if not user_input:
                continue
            if user_input.lower() == "quit":
                break
            if user_input.lower() == "/h":
                _show_help()
                await _handle_menu()
                continue
            if user_input.lower() == "/clear":
                await clear_session_id()
                print("Session cleared.")
                continue
            if user_input.lower().startswith("/mcp "):
                cmd = user_input[5:].strip()
                await _handle_mcp_command(cmd)
                continue
            if user_input.lower() == "/mcp":
                await _cli_mcp_list()
                continue

            deep_reflect = parse_deep_reflect_command(user_input)
            if deep_reflect.get("matched"):
                response = await run_agent(
                    str(deep_reflect.get("focus") or ""),
                    None,
                    0,
                    str(DB_PATH),
                    command=DEEP_REFLECT_COMMAND_ID,
                    public_user_message=user_input,
                )
            else:
                response = await run_agent(user_input, None, 0, str(DB_PATH))
            print(f"\n{ASSISTANT_NAME}: {response}")
        except (KeyboardInterrupt, EOFError):
            break
        except Exception:
            logger.exception("Error in CLI loop")


async def _run_cli_loop_with_shutdown(
) -> None:
    """Historical CLI loop wrapper with its original no-argument contract."""
    try:
        await _cli_loop()
    finally:
        from cyrene.runtime.lifecycle import shutdown_background_work

        await shutdown_background_work()


async def _run_cli_loop_with_application(
    application: ApplicationLifecycle,
) -> None:
    """Run the CLI and shut down the lifecycle initialized for this host."""
    try:
        await _cli_loop()
    finally:
        await application.shutdown()


async def _run_cli_host() -> None:
    """Run CLI setup, interaction, and teardown in one event loop."""
    application = await _prepare_application()
    try:
        from cyrene.runtime.setup import init_setup_flag, is_setup_done, run_setup

        init_setup_flag()
        if not is_setup_done():
            await run_setup()
        await _run_cli_loop_with_application(application)
    except BaseException:
        await application.shutdown()
        raise


def _run_electron_mode() -> None:
    """Start web UI mode for Electron embedding.

    Similar to _run_web_mode() but uses 127.0.0.1, dynamic port,
    fire-and-forget background services, and prints PORT=<n> to stdout
    so Electron can discover the server.
    """
    import sys as _sys
    if "--agent" in _sys.argv:
        ui_mode = "legacy"
    elif "--workbench" in _sys.argv:
        ui_mode = "workbench"
    else:
        ui_mode = _get_default_ui_mode()
    if "--verbose" in _sys.argv:
        import cyrene.observability.debug as _debug
        _debug.VERBOSE = True
        _debug.init_debug_log()

    # On Windows, console=False makes stdout/stderr None.
    # Redirect to devnull to prevent uvicorn formatters from crashing.
    if _sys.stdout is None:
        import os as _os
        _sys.stdout = open(_os.devnull, "w")
    if _sys.stderr is None:
        import os as _os
        _sys.stderr = open(_os.devnull, "w")

    # Prevent ALL subprocesses from creating console windows on Windows.
    # Our backend has no console (console=False), so any subprocess spawned
    # by dependencies (e.g. git calls from vendored searx) would get a new
    # console window unless CREATE_NO_WINDOW is specified.
    # Monkey-patch subprocess.Popen to inject CREATE_NO_WINDOW + SW_HIDE
    # on every call — there is no clean global default in Python 3.13.
    if _sys.platform == "win32":
        import subprocess as _sp
        _orig_popen_init = _sp.Popen.__init__
        _CREATE_NO_WINDOW = 0x08000000
        def _patched_popen_init(self, *args, **kwargs):
            kwargs['creationflags'] = kwargs.get('creationflags', 0) | _CREATE_NO_WINDOW
            if 'startupinfo' not in kwargs or kwargs['startupinfo'] is None:
                _si = _sp.STARTUPINFO()
                _si.dwFlags = _sp.STARTF_USESHOWWINDOW
                _si.wShowWindow = 0  # SW_HIDE
                kwargs['startupinfo'] = _si
            _orig_popen_init(self, *args, **kwargs)
        _sp.Popen.__init__ = _patched_popen_init

    import asyncio
    from cyrene.runtime.scheduler import setup_scheduler
    from webui.server import create_app, WebBot

    selected_port = _pick_web_port(WEB_PORT)
    instance_id = uuid.uuid4().hex

    async def _start():
        application = ApplicationLifecycle(
            create_runtime_context(host_mode="electron")
        )
        await application.initialize(events=True, learning=True)

        bot = WebBot()
        scheduler = setup_scheduler(bot, str(DB_PATH))
        scheduler.start()
        application.register_manager(
            "scheduler",
            scheduler,
            close=lambda: scheduler.shutdown(wait=False),
        )

        application.start_update_check()

        # Fire-and-forget: background services don't block server start
        application.create_task(
            application.start_external_services(),
            label="external service startup",
        )

        app = create_app(bot, str(DB_PATH), instance_id=instance_id, ui_mode=ui_mode)
        import uvicorn
        config = uvicorn.Config(app, host="127.0.0.1", port=selected_port, log_level="info")
        server = uvicorn.Server(config)

        # Monkey-patch startup so we only tell Electron the port AFTER the
        # uvicorn server is actually listening.  Previously PORT was printed
        # before server.serve() — Electron got the port, navigated to the URL,
        # but the server wasn't ready yet → white screen.
        _orig_startup = server.startup

        async def _startup_and_notify(sockets=None):
            await _orig_startup(sockets=sockets)
            if not server.should_exit:
                # Tell Electron which UI is being served (before PORT) so it can
                # pick the matching window chrome: the workbench draws its own
                # inset title bar, the legacy/agent UI needs the native one.
                print(f"UIMODE={ui_mode}", flush=True)
                print(f"PORT={selected_port}", flush=True)

        server.startup = _startup_and_notify

        try:
            await server.serve()
        finally:
            # SIGINT may cancel the main coroutine while it is already inside
            # this finalizer. Give the owned cleanup task a cancellation shield
            # so scheduler/background teardown still completes before exit.
            await _shielded_application_shutdown(application)

    try:
        asyncio.run(_start())
    except KeyboardInterrupt:
        logger.info("Electron backend stopped by user")


def _run_web_mode(ui_mode: str = "workbench") -> None:
    """Start web UI mode."""
    import sys as _sys
    if "--verbose" in _sys.argv:
        import cyrene.observability.debug as _debug
        _debug.VERBOSE = True
        _debug.init_debug_log()
    requested_port = _read_int_flag(_sys.argv[1:], "--port")
    preferred_port = requested_port or WEB_PORT
    selected_port = _pick_web_port(preferred_port)

    import asyncio
    from cyrene.runtime.scheduler import setup_scheduler
    from webui.server import run_web, WebBot

    async def _start():
        application = ApplicationLifecycle(
            create_runtime_context(host_mode="web")
        )
        await application.initialize(events=True, learning=True)
        await application.start_external_services()

        bot = WebBot()
        scheduler = setup_scheduler(bot, str(DB_PATH))
        scheduler.start()
        application.register_manager(
            "scheduler",
            scheduler,
            close=lambda: scheduler.shutdown(wait=False),
        )
        print(f"{ASSISTANT_NAME} Web UI starting at http://127.0.0.1:{selected_port}")
        if selected_port != preferred_port:
            print(f"Port {preferred_port} is busy; using {selected_port} instead.")

        # 后台检查更新（不阻塞启动）
        application.start_update_check()

        try:
            await run_web(bot, str(DB_PATH), port=selected_port, ui_mode=ui_mode)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await _shielded_application_shutdown(application)

    try:
        asyncio.run(_start())
    except KeyboardInterrupt:
        logger.info("Web backend stopped by user")


def _dump_error(message: str) -> None:
    """Write an error message to temp files so the user can inspect it."""
    import os as _os
    _paths = []
    try:
        from cyrene.runtime.paths import TEMP_DIR as _CYRENE_TEMP_DIR
        _paths.append(str(_CYRENE_TEMP_DIR))
    except Exception:
        pass
    for _key in ("TMPDIR", "TEMP", "TMP"):
        if _os.environ.get(_key):
            _paths.append(_os.environ[_key])
    # Fallback: write next to the executable or current directory
    try:
        _paths.append(str(_os.path.dirname(_os.path.abspath(_os.path.realpath(__file__)))))
    except Exception:
        pass
    for _dir in _paths:
        try:
            _os.makedirs(_dir, exist_ok=True)
            _log_path = _os.path.join(_dir, "cyrene_error.log")
            with open(_log_path, "a", encoding="utf-8") as _f:
                _f.write(message + "\n")
        except Exception:
            pass


def _show_error(title: str, message: str) -> None:
    """Show an error to the user, preferring a native dialog on Windows
    (where console=False hides stderr)."""
    import sys as _sys
    if _sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
            return
        except Exception:
            pass  # MessageBoxW failed — try fallback below
    print(f"{title}: {message}", file=_sys.stderr)


def _run_web_gui() -> None:
    """Start web UI with native desktop window (PyInstaller GUI mode).

    Server init runs in a background thread; pywebview window on the main thread.
    """
    import sys as _sys
    if "--agent" in _sys.argv:
        ui_mode = "legacy"
    elif "--workbench" in _sys.argv:
        ui_mode = "workbench"
    else:
        ui_mode = _get_default_ui_mode()
    if "--verbose" in _sys.argv:
        import cyrene.observability.debug as _debug
        _debug.VERBOSE = True
        _debug.init_debug_log()

    # On Windows GUI mode (console=False in PyInstaller), sys.stdout and
    # sys.stderr are None.  uvicorn and its logging formatters
    # (DefaultFormatter -> sys.stdout.isatty()) crash on None.
    if _sys.stdout is None:
        import os as _os
        _sys.stdout = open(_os.devnull, "w")
    if _sys.stderr is None:
        import os as _os
        _sys.stderr = open(_os.devnull, "w")

    import asyncio
    import threading
    import time
    from pathlib import Path
    from cyrene.runtime.scheduler import setup_scheduler
    from webui.server import create_app, WebBot

    selected_port = _pick_web_port(WEB_PORT)
    instance_id = uuid.uuid4().hex
    server_failed = threading.Event()
    server_error: list[str] = []

    async def _start_all():
        application = ApplicationLifecycle(
            create_runtime_context(host_mode="gui")
        )
        await application.initialize(events=True)

        bot = WebBot()
        scheduler = setup_scheduler(bot, str(DB_PATH))
        scheduler.start()
        application.register_manager(
            "scheduler",
            scheduler,
            close=lambda: scheduler.shutdown(wait=False),
        )

        application.start_update_check()

        # Fire-and-forget: SearXNG + MCP start in the background so the
        # web server is available immediately (SearXNG health-check can
        # take up to 30 s, which would otherwise cause "Server not responding").
        application.create_task(
            application.start_external_services(),
            label="external service startup",
        )

        app = create_app(bot, str(DB_PATH), instance_id=instance_id, ui_mode=ui_mode)
        import uvicorn
        config = uvicorn.Config(app, host="127.0.0.1", port=selected_port, log_level="info")
        server = uvicorn.Server(config)
        try:
            await server.serve()
        finally:
            await application.shutdown()

    def _run_server():
        try:
            asyncio.run(_start_all())
        except Exception as exc:
            server_error.append(str(exc))
            server_failed.set()

    _server_thread = threading.Thread(target=_run_server, daemon=True)
    _server_thread.start()

    url = f"http://127.0.0.1:{selected_port}"

    # Wait for the server to start listening (raw TCP, no HTTP, no proxy).
    # Try up to 60 times × 0.5s = 30s.  If it still fails, warn and proceed
    # anyway — the server might be slow but will come online eventually.
    import socket as _socket
    _sock_ok = False
    for _ in range(60):
        if server_failed.is_set():
            break
        try:
            _s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            _s.settimeout(0.5)
            _s.connect(("127.0.0.1", selected_port))
            _s.close()
            _sock_ok = True
            break
        except Exception:
            time.sleep(0.25)
    if not _sock_ok and not server_failed.is_set():
        _show_error("Cyrene - Server Starting",
                     f"Web server is taking longer than expected.\n"
                     f"If it doesn't load automatically, open:\n"
                     f"{url}")

    if server_failed.is_set():
        _show_error("Cyrene - Server Error", server_error[0] if server_error else "Server failed to start.")
        _sys.exit(1)

    # macOS: use compiled Swift WKWebView helper (native, zero deps).
    # Give it a short grace period, then verify the window actually appeared
    # by checking whether the process is still alive.  If not — fall through
    # and let the user open the URL in their browser.
    if _sys.platform == "darwin":
        _bin = Path(_sys._MEIPASS) / "cyrene_window" if getattr(_sys, "frozen", False) else Path(__file__).resolve().parent.parent.parent / "build" / "cyrene_window"
        if _bin.exists():
            import subprocess
            proc = subprocess.Popen([str(_bin), url])
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Process is still alive — window was shown successfully
                proc.wait()
                return
            # Process exited within 3 s — window likely failed to appear
            logger.warning("cyrene_window exited early (rc=%d), falling back to browser", proc.returncode)

    # Windows/Linux: try pywebview
    try:
        import webview
    except ImportError:
        _show_error("Cyrene - Missing Dependency",
                     "pywebview is not installed.\n\n"
                     "Install it with: pip install pywebview>=5.0")
        _sys.exit(1)

    # On Windows, the Edge Chromium backend requires WebView2 Runtime.
    # Detect this early so we can give a specific error message instead
    # of a generic pywebview crash.
    if _sys.platform == "win32":
        try:
            from webview.platforms.edgechromium import _version as edge_v  # noqa: F401
        except Exception:
            _show_error("Cyrene - WebView2 Required",
                         "Microsoft Edge WebView2 Runtime is not installed.\n\n"
                         "Download it from:\n"
                         "https://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n"
                         "After installing, restart Cyrene.")
            _fallback_to_browser(url, _server_thread)

    try:
        webview.create_window("Cyrene", url, width=1200, height=800, min_size=(800, 600))
        webview.start()
    except Exception as exc:
        logger.warning("pywebview failed (%s)", exc)
        _dump_error(f"pywebview failed: {exc}")
        _hint = ""
        if _sys.platform == "win32":
            _hint = ("\n\nOn Windows this usually means the Edge WebView2 Runtime\n"
                     "is missing. Download from:\n"
                     "https://go.microsoft.com/fwlink/p/?LinkId=2124703")
        _show_error("Cyrene - Window Error",
                     f"Failed to create native window:\n{exc}{_hint}\n\n"
                     f"Server running at {url}\n"
                     "Open this address in your browser.")
        _fallback_to_browser(url, _server_thread)


def _fallback_to_browser(url: str, _server_thread=None) -> None:
    """Open the web UI in the default browser and keep the process alive."""
    print(f"Cyrene server is running at {url}", flush=True)
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    print("Press Ctrl+C to stop.", flush=True)
    import http.client
    _health_host = "127.0.0.1"
    _health_port = int(url.rsplit(":", 1)[1])
    _health_skip = 0
    try:
        while True:
            import time
            time.sleep(5)
            # Periodically check if the server thread is still alive.
            # It could exit silently if the asyncio loop inside crashes.
            if _server_thread is not None and not _server_thread.is_alive():
                _health_skip += 1
                if _health_skip > 3:  # 3 × 5s = 15s grace period
                    print("Server stopped responding — keeping process alive for browser connections.", flush=True)
            # Also try a lightweight health check to detect frozen server.
            if _health_skip <= 0:
                try:
                    _conn = http.client.HTTPConnection(_health_host, _health_port, timeout=2.0)
                    _conn.request("GET", "/api/instance-id")
                    _conn.getresponse().read()
                    _conn.close()
                except Exception:
                    pass
    except KeyboardInterrupt:
        pass


async def _run_one_shot_mcp(args: list[str]) -> None:
    """Run a single MCP command and exit."""
    application = await _prepare_application()
    try:
        cmd_line = " ".join(args)
        await _handle_mcp_command(cmd_line)
    finally:
        await application.shutdown()


def run_web_mode(ui_mode: str = "workbench") -> None:
    """Public host entry point used by ``python -m cyrene``."""
    _run_web_mode(ui_mode=ui_mode)


def main() -> None:
    import sys
    if "--electron-mode" in sys.argv:
        _run_electron_mode()
        return
    if "--gui" in sys.argv:
        _run_web_gui()
        return
    if "--workbench" in sys.argv:
        _run_web_mode(ui_mode="workbench")
        return
    if "--agent" in sys.argv:
        _run_web_mode(ui_mode="legacy")
        return
    if "--web" in sys.argv:
        _run_web_mode(ui_mode="workbench")
        return

    # One-shot MCP commands (no interactive loop)
    mcp_args = [a for a in sys.argv[1:] if a.startswith("--mcp-")]
    if mcp_args:
        for flag in mcp_args:
            idx = sys.argv.index(flag)
            if flag == "--mcp-list":
                asyncio.run(_run_one_shot_mcp(["list"]))
            elif flag == "--mcp-test" and idx + 1 < len(sys.argv):
                asyncio.run(_run_one_shot_mcp(["test", sys.argv[idx + 1]]))
            elif flag == "--mcp-add":
                # --mcp-add name stdio command arg1 arg2 ...  OR  --mcp-add name sse url
                rest = sys.argv[idx + 1:]
                asyncio.run(_run_one_shot_mcp(["add"] + rest))
                break
            elif flag == "--mcp-remove" and idx + 1 < len(sys.argv):
                asyncio.run(_run_one_shot_mcp(["remove", sys.argv[idx + 1]]))
            elif flag == "--mcp-toggle" and idx + 1 < len(sys.argv):
                asyncio.run(_run_one_shot_mcp(["toggle", sys.argv[idx + 1]]))
            else:
                print("Usage: --mcp-list | --mcp-test <name> | --mcp-add <name> stdio <cmd> [args...] | --mcp-add <name> sse <url> | --mcp-remove <name> | --mcp-toggle <name>")
        return

    if "--verbose" in sys.argv:
        import cyrene.observability.debug as _debug
        _debug.VERBOSE = True
        _debug.init_debug_log()
        lp = _debug.get_log_path()
        if lp:
            print(f"Debug log: {lp}")

    asyncio.run(_run_cli_host())


if __name__ == "__main__":
    main()
