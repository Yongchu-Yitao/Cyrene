import asyncio
import logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
_CLIENT_COMMANDS = frozenset(
    {
        "chat",
        "mcp",
        "memory",
        "session",
        "start",
        "status",
        "stop",
    }
)


def _print_help() -> None:
    print(
        """usage: python -m cyrene [mode] [options]

Cyrene runtime entry point.

modes:
  (default)         Start the Workbench web UI
  --workbench       Start the Workbench web UI
  --gui              Start the native GUI wrapper
  --telegram         Start the Telegram bot

options:
  --port PORT        Workbench web server port
  --verbose, -v      Enable verbose diagnostics
  -h, --help         Show this help without initializing the runtime

The installed `cyrene` command provides daemon/client subcommands; run
`cyrene --help` for those commands.
"""
    )


async def _prepare_runtime() -> None:
    """初始化运行时所需的目录和文件"""
    from cyrene.config import (
        DB_PATH,
        INBOX_DIR,
    )
    from cyrene.runtime.bootstrap import initialize_runtime

    await initialize_runtime()
    logger.info("Database initialized at %s", DB_PATH)
    logger.info("Inbox ready at %s", INBOX_DIR)
    # 人格设置检测（Telegram 模式跳过交互，提示用户先运行 CLI）
    from cyrene.runtime.setup import init_setup_flag, is_setup_done
    init_setup_flag()
    if not is_setup_done():
        logger.warning("首次启动检测到未设置人格。请先运行 CLI 模式完成设置：")
        logger.warning("  python -m cyrene.runtime.host")


def _run_plugin_launcher(name: str) -> None:
    """Run a named launcher contributed by one enabled editable Plugin pack."""

    from cyrene.core.plugin import PluginRegistry, default_plugin_impl_directory
    from cyrene.plugins.native_tools import seed_builtin_plugin_directory
    from cyrene.runtime import settings_store

    plugin_directory = default_plugin_impl_directory()
    seed_builtin_plugin_directory(plugin_directory)
    registry = PluginRegistry(include_core=False)
    failures = registry.load_directory(plugin_directory)
    registry.configure_activation(
        plugins=settings_store.get_enabled_plugins(),
        packs=settings_store.get_enabled_plugin_packs(),
    )
    candidates = []
    for pack in registry.list_packs():
        if not registry.pack_enabled(pack.id):
            continue
        launchers = pack.metadata.get("runtime_launchers", {})
        if isinstance(launchers, dict) and callable(launchers.get(name)):
            candidates.append((pack.id, launchers[name]))
    if failures:
        logger.warning(
            "Some Plugin launchers failed to load: %s",
            "; ".join(f"{item.path}: {item.error}" for item in failures),
        )
    if len(candidates) != 1:
        owners = ", ".join(pack_id for pack_id, _ in candidates) or "none"
        raise RuntimeError(
            f"Expected one enabled Plugin launcher for {name!r}; found {owners}."
        )
    candidates[0][1]()


def main() -> None:
    import sys
    if sys.argv[1:2] and sys.argv[1] in _CLIENT_COMMANDS:
        from cyrene.cli import main as client_main

        client_main()
        return
    if "--help" in sys.argv or "-h" in sys.argv:
        _print_help()
        return
    # File logging must not touch the filesystem on the --help path.
    from cyrene.observability.logging_setup import setup_persistent_logging

    setup_persistent_logging()
    # GUI-launched Electron inherits LaunchServices' minimal PATH; pull the
    # user's login-shell PATH so ACP agents and toolchain subprocesses can
    # find shell-managed runtimes (nvm, Homebrew, mise).
    from cyrene.runtime.user_path import ensure_user_path

    ensure_user_path()
    if "--gui" in sys.argv or "--electron-mode" in sys.argv:
        from cyrene.runtime.host import main as _local_main
        _local_main()
        return
    if "--telegram" in sys.argv:
        asyncio.run(_prepare_runtime())
        _run_plugin_launcher("telegram")
        return
    from cyrene.runtime.host import run_web_mode
    run_web_mode(ui_mode="workbench")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception:
        logger.exception("Fatal error")
