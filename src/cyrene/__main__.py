import asyncio
import logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
_runtime_started = False


def _print_help() -> None:
    print(
        """usage: python -m cyrene [mode] [options]

Cyrene runtime entry point.

modes:
  (default)         Start the Workbench web UI
  --workbench       Start the Workbench web UI (compatibility alias)
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
        DATA_DIR,
        DB_PATH,
        INBOX_DIR,
        SOUL_PATH,
    )
    from cyrene.runtime.bootstrap import initialize_runtime, start_external_services

    await initialize_runtime(learning=True)
    logger.info("Database initialized at %s", DB_PATH)
    logger.info("SOUL.md ready at %s", SOUL_PATH)
    logger.info("Inbox ready at %s", INBOX_DIR)
    logger.info("Short-term memory initialized at %s", DATA_DIR / "short_term.json")
    await start_external_services()

    # 人格设置检测（Telegram 模式跳过交互，提示用户先运行 CLI）
    from cyrene.runtime.setup import init_setup_flag, is_setup_done
    init_setup_flag()
    if not is_setup_done():
        logger.warning("首次启动检测到未设置人格。请先运行 CLI 模式完成设置：")
        logger.warning("  python -m cyrene.runtime.host")


def _run_bot() -> None:
    from cyrene.channels.telegram import setup_bot
    from cyrene.config import ASSISTANT_NAME

    app = setup_bot()
    logger.info("%s is starting...", ASSISTANT_NAME)
    app.run_polling()


def main() -> None:
    import sys
    global _runtime_started
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
        _runtime_started = True
        from cyrene.runtime.host import main as _local_main
        _local_main()
        return
    if "--telegram" in sys.argv:
        _runtime_started = True
        asyncio.run(_prepare_runtime())
        _run_bot()
        return
    _runtime_started = True
    from cyrene.runtime.host import run_web_mode
    run_web_mode(ui_mode="workbench")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception:
        logger.exception("Fatal error")
    finally:
        if _runtime_started:
            from cyrene.runtime.bootstrap import stop_external_services

            stop_external_services()
