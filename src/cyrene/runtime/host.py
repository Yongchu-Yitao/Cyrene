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

from cyrene.config import (
    ASSISTANT_NAME,
    DB_PATH,
    WORKSPACE_DIR,
    WEB_PORT,
)
from cyrene.localization import localized
from cyrene.runtime.application import ApplicationLifecycle
from cyrene.runtime.bootstrap import create_runtime_context

logger = logging.getLogger(__name__)
_CLI_SESSION_ID = "cli"


class _CliRun:
    """Small event sink used by the terminal UI's Plugin-backed conversation."""

    def __init__(self) -> None:
        self.run_id = "run_" + uuid.uuid4().hex

    async def publish(self, _event: dict[str, object]) -> None:
        return None


async def _run_cli_agent(user_input: str) -> str:
    """Execute one CLI turn through the same AgentSession used by Workbench."""

    from cyrene.workbench.core_adapter.chat_runtime import run_workbench_chat

    result = await run_workbench_chat(
        run=_CliRun(),
        user_message=str(user_input or ""),
        bot=None,
        host_chat_id=0,
        db_path=str(DB_PATH),
        session_id=_CLI_SESSION_ID,
        workspace_dir=str(WORKSPACE_DIR),
        public_user_message=str(user_input or ""),
        conversation_source="cli",
    )
    return result.text


async def _clear_cli_context() -> None:
    """Delete only the durable ContextTree owned by the terminal UI."""

    from cyrene.core.context import ContextStoreRouter, TreeNotFoundError
    from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory

    router = ContextStoreRouter(
        workbench_agent_data_directory(str(DB_PATH)) / "context"
    )
    try:
        try:
            await asyncio.to_thread(router.delete_tree, _CLI_SESSION_ID)
        except TreeNotFoundError:
            pass
    finally:
        router.close()


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


def _normalize_ui_mode(ui_mode: str | None) -> str:
    """Normalize historical UI mode values to the sole Workbench surface."""
    return "workbench"


def _get_default_ui_mode() -> str:
    """Return the normalized UI mode baked in at build time."""
    try:
        from cyrene.runtime.buildinfo import DEFAULT_UI_MODE
        return _normalize_ui_mode(DEFAULT_UI_MODE)
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
    logger.error("Failed to allocate a local web port (preferred=%s)", preferred_port)
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

    # CLI and one-shot hosts use the exact same editable Plugin application
    # contributions as Workbench.  A headless router is sufficient: it creates
    # the model, memory, content, MCP, and schedule services without serving an
    # HTTP app, and keeps their lifecycle owned by this ApplicationLifecycle.
    from fastapi import APIRouter, FastAPI
    from cyrene.plugins import (
        PluginApplicationHost,
        application_plugin_scope,
        set_application_plugin_scope,
    )

    plugin_host = PluginApplicationHost.load_user_plugins(
        app=FastAPI(),
        bot=None,
        db_path=str(application.context.database_path),
        data_directory=application.context.paths.data,
    )
    plugin_host.attach(APIRouter())
    set_application_plugin_scope(plugin_host)
    await plugin_host.startup()

    async def close_plugin_host() -> None:
        await plugin_host.shutdown()
        if application_plugin_scope() is plugin_host:
            set_application_plugin_scope(None)

    application.register_manager(
        "plugin_application",
        plugin_host,
        close=close_plugin_host,
    )
    return application


async def _prepare_cli() -> None:
    """Historical setup hook retained with its original no-argument contract."""
    await _prepare_application()


# ---------------------------------------------------------------------------
# MCP CLI helpers (shared between menu and command-line flags)
# ---------------------------------------------------------------------------


def _cli_mcp_service():
    from cyrene.core.plugin import application_plugin_service

    service = application_plugin_service("mcp")
    if service is None:
        raise RuntimeError(localized(
            "The MCP Plugin is disabled or unavailable.",
            "MCP 插件已禁用或不可用。",
        ))
    return service


def _cli_mcp_status_label(value: object) -> str:
    normalized = str(value or "disconnected").strip().lower()
    return {
        "connected": localized("connected", "已连接"),
        "connecting": localized("connecting", "连接中"),
        "disconnected": localized("disconnected", "未连接"),
        "disabled": localized("disabled", "已禁用"),
        "error": localized("error", "错误"),
        "failed": localized("failed", "失败"),
    }.get(normalized, normalized)


async def _cli_mcp_list() -> None:
    service = _cli_mcp_service()
    configs = service.configs()
    if not configs:
        print(localized("  No MCP servers configured.", "  尚未配置 MCP 服务器。"))
        return
    statuses = {s["name"]: s for s in service.status()}
    print("\n" + localized(
        "  {name:<16} {transport:<10} {status:<14} {tools:<6} Endpoint",
        "  {name:<16} {transport:<10} {status:<14} {tools:<6} 接口",
        name=localized("Name", "名称"),
        transport=localized("Transport", "传输"),
        status=localized("Status", "状态"),
        tools=localized("Tools", "工具"),
    ))
    print(f"  {'-'*16} {'-'*10} {'-'*14} {'-'*6} {'-'*40}")
    for cfg in configs:
        name = cfg.get("name", "?")
        st = statuses.get(name, {})
        status = _cli_mcp_status_label(st.get("status", "disconnected"))
        tools = st.get("tool_count", 0)
        transport = cfg.get("transport", "stdio")
        endpoint = cfg.get("command", "") if transport == "stdio" else cfg.get("url", "")
        enabled = cfg.get("enabled", True)
        enabled_mark = "" if enabled else localized(" [disabled]", " [已禁用]")
        print(f"  {name:<16} {transport:<10} {status:<14} {tools:<6} {endpoint}{enabled_mark}")
    tools = [
        tool
        for status in statuses.values()
        for tool in status.get("tools") or ()
    ]
    if tools:
        print("\n" + localized(
            "  Total MCP Plugins available: {count}",
            "  可用 MCP 插件总数：{count}",
            count=len(tools),
        ))
        for tool in tools:
            print(
                f"    - {tool.get('plugin') or tool.get('name')}: "
                f"{str(tool.get('description') or '')[:80]}"
            )


async def _cli_mcp_add(args: list[str]) -> None:
    if len(args) < 3:
        print(localized(
            "  Usage: add <name> stdio <command> [args...]",
            "  用法：add <名称> stdio <命令> [参数...]",
        ))
        print(localized(
            "         add <name> sse <url>",
            "        add <名称> sse <网址>",
        ))
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
        print(localized(
            "  Unknown transport: {transport} (use stdio or sse)",
            "  未知传输方式：{transport}（请使用 stdio 或 sse）",
            transport=transport,
        ))
        return
    service = _cli_mcp_service()
    servers = service.configs()
    servers = [s for s in servers if s.get("name") != name]
    servers.append(server)
    await service.replace_configs(servers)
    status = service.server_status(name) or {}
    state = str(status.get("status") or "disconnected")
    if state == "connected":
        print(localized(
            "  ✅ MCP server '{name}' was added and connected.",
            "  ✅ MCP 服务器“{name}”已添加并连接。",
            name=name,
        ))
    else:
        print(localized(
            "  MCP server '{name}' was saved but could not connect.",
            "  MCP 服务器“{name}”已保存，但无法连接。",
            name=name,
        ))


async def _cli_mcp_remove(args: list[str]) -> None:
    if not args:
        print(localized("  Usage: remove <name>", "  用法：remove <名称>"))
        return
    name = args[0]
    removed = await _cli_mcp_service().remove(name)
    if removed is None:
        print(localized(
            "  Server '{name}' was not found.",
            "  未找到服务器“{name}”。",
            name=name,
        ))
        return
    print(localized(
        "  ✅ MCP server '{name}' was removed.",
        "  ✅ 已移除 MCP 服务器“{name}”。",
        name=name,
    ))


async def _cli_mcp_toggle(args: list[str]) -> None:
    if not args:
        print(localized("  Usage: toggle <name>", "  用法：toggle <名称>"))
        return
    name = args[0]
    service = _cli_mcp_service()
    existing = next(
        (item for item in service.configs() if item.get("name") == name),
        None,
    )
    if existing is None:
        print(localized(
            "  Server '{name}' was not found.",
            "  未找到服务器“{name}”。",
            name=name,
        ))
        return
    enabled = not bool(existing.get("enabled", True))
    await service.set_enabled(name, enabled)
    status = localized("enabled", "已启用") if enabled else localized("disabled", "已禁用")
    print(localized(
        "  ✅ MCP server '{name}' is {status}.",
        "  ✅ MCP 服务器“{name}”{status}。",
        name=name,
        status=status,
    ))


async def _cli_mcp_test(args: list[str]) -> None:
    if not args:
        print(localized("  Usage: test <name>", "  用法：test <名称>"))
        return
    name = args[0]
    status = _cli_mcp_service().server_status(name)
    if status and status.get("status") == "connected":
        tools = list(status.get("tools") or ())
        print(localized(
            "  ✅ Server '{name}' is connected; {count} tools are available.",
            "  ✅ 服务器“{name}”已连接，可用工具 {count} 个。",
            name=name,
            count=len(tools),
        ))
        for tool in tools[:10]:
            print(
                f"    - {tool.get('plugin') or tool.get('name')}: "
                f"{str(tool.get('description') or '')[:60]}"
            )
        if len(tools) > 10:
            print(localized(
                "    ... and {count} more",
                "    ……另有 {count} 个",
                count=len(tools) - 10,
            ))
        return
    print(localized(
        "  Server '{name}' is not connected. Check its configuration with '/mcp list'.",
        "  服务器“{name}”未连接。请使用“/mcp list”检查配置。",
        name=name,
    ))


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
        print(localized(
            "  Unknown MCP command: {command}",
            "  未知 MCP 命令：{command}",
            command=sub,
        ))
        print(localized(
            "  Commands: list, add, remove, toggle, test",
            "  可用命令：list、add、remove、toggle、test",
        ))


def _show_help():
    print()
    print("=" * 40)
    print(localized("  Cyrene Help", "  Cyrene 帮助菜单"))
    print("=" * 40)
    print(localized(
        "  1) Reconfigure personality (run setup again)",
        "  1) 重新注入人格（重新运行设置向导）",
    ))
    print(localized(
        "  2) Clear conversation context (session)",
        "  2) 清除对话上下文（session）",
    ))
    print(localized(
        "  3) Reset personality (restore the default SOUL.md)",
        "  3) 重置人格（恢复默认 SOUL.md）",
    ))
    print(localized("  4) Check system status", "  4) 检查系统状态"))
    print(localized("  0) Return to the conversation", "  0) 返回对话"))
    print("=" * 40)


async def _handle_menu():
    while True:
        choice = input(localized(
            "\nChoose an action (0-4): ",
            "\n选择操作 (0-4): ",
        )).strip()

        if choice == "0":
            print(localized("Returning to the conversation.", "返回对话。"))
            return

        elif choice == "1":
            from cyrene.runtime.setup import init_setup_flag, run_setup
            init_setup_flag()
            print(localized(
                "\n--- Reconfigure personality ---",
                "\n--- 重新注入人格 ---",
            ))
            await run_setup()
            print(localized(
                "Personality setup is complete. Enter /h to configure it again.",
                "人格设置完成。输入 /h 可以重新设置。",
            ))
            return

        elif choice == "2":
            await _clear_cli_context()
            print(localized(
                "✅ Conversation context was cleared.",
                "✅ 对话上下文已清除。",
            ))
            return

        elif choice == "3":
            from cyrene.core.plugin import application_plugin_service

            soul_service = application_plugin_service("soul")
            if soul_service is None:
                print(localized(
                    "❌ The SOUL Plugin is currently unavailable.",
                    "❌ SOUL 插件当前不可用。",
                ))
                return
            memory_service = application_plugin_service("memory")
            soul_service.reset()
            if memory_service is not None:
                memory_service.save_short_term_entries([])
                print(localized(
                    "✅ SOUL.md was reset to the default, and short-term memory was cleared.",
                    "✅ SOUL.md 已重置为默认。短期记忆已清空。",
                ))
            else:
                print(localized(
                    "✅ SOUL.md was reset to the default. The Memory Plugin is not enabled.",
                    "✅ SOUL.md 已重置为默认。记忆插件当前未启用。",
                ))
            return

        elif choice == "4":
            from cyrene.core.plugin import application_plugin_service

            model_service = application_plugin_service("model_configuration")
            primary = model_service.candidates_for_route("primary") if model_service is not None else []
            candidate = primary[0] if primary else {}
            memory_service = application_plugin_service("memory")
            soul_service = application_plugin_service("soul")
            not_configured = localized("Not configured", "未配置")
            print(localized("\n--- System status ---", "\n--- 系统状态 ---"))
            print(localized(
                "  Model: {value}",
                "  模型：{value}",
                value=candidate.get("model") or not_configured,
            ))
            print(localized(
                "  Endpoint: {value}",
                "  地址：{value}",
                value=candidate.get("base_url") or not_configured,
            ))
            soul_path = soul_service.path() if soul_service is not None else None
            soul_state = localized("present", "存在") if soul_path and soul_path.exists() else localized("missing", "不存在")
            soul_location = soul_path or localized(
                "SOUL Plugin unavailable",
                "SOUL 插件不可用",
            )
            print(localized(
                "  SOUL.md: {state} ({location})",
                "  SOUL.md：{state}（{location}）",
                state=soul_state,
                location=soul_location,
            ))
            if soul_path and soul_path.exists() and soul_service is not None:
                soul_content = soul_service.read()
                print(localized(
                    "  Personality content: {count} characters",
                    "  人格内容：{count} 个字符",
                    count=len(soul_content),
                ))
            st_entries = (
                memory_service.short_term_entries()
                if memory_service is not None
                else []
            )
            print(localized(
                "  Short-term memories: {count}",
                "  短期记忆：{count} 条",
                count=len(st_entries),
            ))
            from cyrene.workbench.core_adapter.chat_runtime import workbench_agent_data_directory
            from cyrene.workbench.chat.conversation_context_service import AgentContextRepository

            cli_state = AgentContextRepository(
                workbench_agent_data_directory(str(DB_PATH)) / "context"
            ).read(_CLI_SESSION_ID)
            cli_messages = cli_state.get("messages") if isinstance(cli_state, dict) else []
            print(localized(
                "  Current session: {count} messages",
                "  当前 session：{count} 条消息",
                count=len(cli_messages or []),
            ))
            # Optional Plugin sections never make the generic status view fail.
            mcp_service = application_plugin_service("mcp")
            if mcp_service is None:
                print(localized(
                    "  MCP: Plugin not enabled",
                    "  MCP：插件未启用",
                ))
            else:
                mcp_cfgs = mcp_service.configs()
                if mcp_cfgs:
                    print(localized(
                        "  MCP servers: {count} configured",
                        "  MCP 服务器：已配置 {count} 个",
                        count=len(mcp_cfgs),
                    ))
                    for st in mcp_service.status():
                        print(
                            localized(
                                "    {name}: {status} ({count} tools)",
                                "    {name}：{status}（{count} 个工具）",
                                name=st["name"],
                                status=_cli_mcp_status_label(st["status"]),
                                count=st["tool_count"],
                            )
                        )
            print("------------------")
            return

        else:
            print(localized(
                "Invalid choice. Enter a number from 0 to 4.",
                "无效选择，请输入 0-4。",
            ))


async def _cli_loop() -> None:
    print(localized(
        "{name} CLI mode. '/h' opens the menu, '/clear' resets the session, "
        "'/mcp' manages MCP, and 'quit' exits.",
        "{name} CLI 模式。输入“/h”打开菜单，“/clear”重置会话，"
        "“/mcp”管理 MCP，“quit”退出。",
        name=ASSISTANT_NAME,
    ))
    while True:
        try:
            user_input = input(localized("\nYou: ", "\n你：")).strip()
            if not user_input:
                continue
            if user_input.lower() == "quit":
                break
            if user_input.lower() == "/h":
                _show_help()
                await _handle_menu()
                continue
            if user_input.lower() == "/clear":
                await _clear_cli_context()
                print(localized("Session cleared.", "会话已清除。"))
                continue
            if user_input.lower().startswith("/mcp "):
                cmd = user_input[5:].strip()
                await _handle_mcp_command(cmd)
                continue
            if user_input.lower() == "/mcp":
                await _cli_mcp_list()
                continue

            response = await _run_cli_agent(user_input)
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
    from cyrene.observability.logging_setup import setup_persistent_logging

    setup_persistent_logging()
    import sys as _sys
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
    from cyrene.workbench.webui.server import create_app, WebBot

    selected_port = _pick_web_port(WEB_PORT)
    instance_id = uuid.uuid4().hex

    async def _start():
        application = ApplicationLifecycle(
            create_runtime_context(host_mode="electron")
        )
        await application.initialize(events=True)

        bot = WebBot()
        application.start_update_check()

        app = create_app(
            bot,
            str(DB_PATH),
            instance_id=instance_id,
            ui_mode=ui_mode,
            enable_background_plugins=True,
        )
        app.state.web_port = int(selected_port)
        from cyrene.agent_runtime.model_gateway import configure_model_gateway
        configure_model_gateway(selected_port)
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
                logger.info("Electron backend listening on 127.0.0.1:%s (ui_mode=%s)", selected_port, ui_mode)
                # Tell Electron which UI is being served (before PORT) so it can
                # pick the matching window chrome: the workbench draws its own
                # inset title bar, the legacy/agent UI needs the native one.
                print(f"UIMODE={ui_mode}", flush=True)
                print(f"PORT={selected_port}", flush=True)

        server.startup = _startup_and_notify

        try:
            await server.serve()
        finally:
            logger.info("Electron backend stopped")
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
    from cyrene.observability.logging_setup import setup_persistent_logging

    setup_persistent_logging()
    import sys as _sys
    if "--verbose" in _sys.argv:
        import cyrene.observability.debug as _debug
        _debug.VERBOSE = True
        _debug.init_debug_log()
    requested_port = _read_int_flag(_sys.argv[1:], "--port")
    preferred_port = requested_port or WEB_PORT
    selected_port = _pick_web_port(preferred_port)

    import asyncio
    from cyrene.workbench.webui.server import run_web, WebBot

    async def _start():
        application = ApplicationLifecycle(
            create_runtime_context(host_mode="web")
        )
        await application.initialize(events=True)
        bot = WebBot()
        logger.info(
            "Web UI starting at http://127.0.0.1:%s (ui_mode=%s)", selected_port, ui_mode
        )
        print(localized(
            "{name} Web UI is starting at http://127.0.0.1:{port}",
            "{name} Web UI 正在启动：http://127.0.0.1:{port}",
            name=ASSISTANT_NAME,
            port=selected_port,
        ))
        if selected_port != preferred_port:
            print(localized(
                "Port {preferred} is busy; using {selected} instead.",
                "端口 {preferred} 已被占用，改用端口 {selected}。",
                preferred=preferred_port,
                selected=selected_port,
            ))
            logger.info("Port %s is busy; using %s instead.", preferred_port, selected_port)

        # 后台检查更新（不阻塞启动）
        application.start_update_check()

        try:
            await run_web(bot, str(DB_PATH), port=selected_port, ui_mode=ui_mode)
            logger.info("Web UI stopped")
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
    from cyrene.workbench.webui.server import create_app, WebBot

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
        application.start_update_check()

        app = create_app(
            bot,
            str(DB_PATH),
            instance_id=instance_id,
            ui_mode=ui_mode,
            enable_background_plugins=True,
        )
        app.state.web_port = int(selected_port)
        from cyrene.agent_runtime.model_gateway import configure_model_gateway
        configure_model_gateway(selected_port)
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
        except Exception:
            logger.exception("Cyrene GUI web server failed")
            server_error.append(localized(
                "The server failed to start.",
                "服务器启动失败。",
            ))
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
        _show_error(
            localized("Cyrene - Server Starting", "Cyrene - 服务器启动中"),
            localized(
                "The web server is taking longer than expected.\n"
                "If it does not load automatically, open:\n{url}",
                "Web 服务器启动时间超出预期。\n"
                "如果没有自动加载，请打开：\n{url}",
                url=url,
            ),
        )

    if server_failed.is_set():
        _show_error(
            localized("Cyrene - Server Error", "Cyrene - 服务器错误"),
            server_error[0] if server_error else localized(
                "The server failed to start.",
                "服务器启动失败。",
            ),
        )
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
        _show_error(
            localized("Cyrene - Missing Dependency", "Cyrene - 缺少依赖"),
            localized(
                "pywebview is not installed.\n\n"
                "Install it with: pip install pywebview>=5.0",
                "尚未安装 pywebview。\n\n"
                "请运行以下命令安装：pip install pywebview>=5.0",
            ),
        )
        _sys.exit(1)

    # On Windows, the Edge Chromium backend requires WebView2 Runtime.
    # Detect this early so we can give a specific error message instead
    # of a generic pywebview crash.
    if _sys.platform == "win32":
        try:
            from webview.platforms.edgechromium import _version as edge_v  # noqa: F401
        except Exception:
            _show_error(
                localized("Cyrene - WebView2 Required", "Cyrene - 需要 WebView2"),
                localized(
                    "Microsoft Edge WebView2 Runtime is not installed.\n\n"
                    "Download it from:\n"
                    "https://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n"
                    "After installing, restart Cyrene.",
                    "尚未安装 Microsoft Edge WebView2 Runtime。\n\n"
                    "请从以下地址下载：\n"
                    "https://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n"
                    "安装后请重启 Cyrene。",
                ),
            )
            _fallback_to_browser(url, _server_thread)

    try:
        webview.create_window("Cyrene", url, width=1200, height=800, min_size=(800, 600))
        webview.start()
    except Exception as exc:
        logger.warning("pywebview failed", exc_info=True)
        _dump_error(f"pywebview failed: {exc}")
        hint = ""
        if _sys.platform == "win32":
            hint = localized(
                "\n\nOn Windows, check that Edge WebView2 Runtime is installed:\n"
                "https://go.microsoft.com/fwlink/p/?LinkId=2124703",
                "\n\n在 Windows 上，请确认已安装 Edge WebView2 Runtime：\n"
                "https://go.microsoft.com/fwlink/p/?LinkId=2124703",
            )
        _show_error(
            localized("Cyrene - Window Error", "Cyrene - 窗口错误"),
            localized(
                "The native window could not be created.{hint}\n\n"
                "The server is running at {url}.\n"
                "Open this address in your browser.",
                "无法创建原生窗口。{hint}\n\n"
                "服务器正在 {url} 运行。\n"
                "请在浏览器中打开此地址。",
                hint=hint,
                url=url,
            ),
        )
        _fallback_to_browser(url, _server_thread)


def _fallback_to_browser(url: str, _server_thread=None) -> None:
    """Open the web UI in the default browser and keep the process alive."""
    print(localized(
        "Cyrene server is running at {url}",
        "Cyrene 服务器正在 {url} 运行",
        url=url,
    ), flush=True)
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    print(localized("Press Ctrl+C to stop.", "按 Ctrl+C 停止。"), flush=True)
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
                    print(localized(
                        "The server stopped responding; the process will remain available for browser connections.",
                        "服务器已停止响应；进程将继续保留以供浏览器连接。",
                    ), flush=True)
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
    _run_web_mode(ui_mode=_normalize_ui_mode(ui_mode))


def main() -> None:
    import sys
    # Electron and local_cli.py launches never pass through cyrene.__main__,
    # so the idempotent login-shell PATH merge must also run here for
    # GUI-launched processes with LaunchServices' minimal PATH.
    from cyrene.runtime.user_path import ensure_user_path

    ensure_user_path()
    if "--electron-mode" in sys.argv:
        _run_electron_mode()
        return
    if "--gui" in sys.argv:
        _run_web_gui()
        return
    if "--workbench" in sys.argv:
        _run_web_mode(ui_mode="workbench")
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
                print(localized(
                    "Usage: --mcp-list | --mcp-test <name> | --mcp-add <name> "
                    "stdio <cmd> [args...] | --mcp-add <name> sse <url> | "
                    "--mcp-remove <name> | --mcp-toggle <name>",
                    "用法：--mcp-list | --mcp-test <名称> | --mcp-add <名称> "
                    "stdio <命令> [参数...] | --mcp-add <名称> sse <网址> | "
                    "--mcp-remove <名称> | --mcp-toggle <名称>",
                ))
        return

    if "--verbose" in sys.argv:
        import cyrene.observability.debug as _debug
        _debug.VERBOSE = True
        _debug.init_debug_log()
        lp = _debug.get_log_path()
        if lp:
            print(localized(
                "Debug log: {path}",
                "调试日志：{path}",
                path=lp,
            ))

    asyncio.run(_run_cli_host())


if __name__ == "__main__":
    main()
