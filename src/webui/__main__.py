"""Web UI entry point — python -m cyrene.webui"""

import asyncio
import logging

from cyrene.config import (
    DATA_DIR,
    DB_PATH,
    INBOX_DIR,
    SEARXNG_AUTO_START,
    SEARXNG_HOST,
    SEARXNG_PORT,
    STORE_DIR,
    TEMP_DIR,
    WORKSPACE_DIR,
)
from cyrene.observability.debug import enable_event_bus
from cyrene.runtime.database import init_db
from cyrene.runtime.inbox import ensure_inbox
from cyrene.runtime.memory.short_term import init_short_term
from cyrene.runtime.memory.soul import ensure_soul
from cyrene.runtime.paths import cleanup_temporary_artifacts
from cyrene.runtime.bootstrap import (
    initialize_runtime,
    start_external_services,
    start_update_check,
    stop_external_services,
    stop_runtime_tasks,
)
from cyrene.runtime.scheduler import setup_scheduler
from webui.server import run_web, WebBot

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    await initialize_runtime(
        events=True,
        learning=True,
        include_temp=True,
        clean_temp=True,
    )
    await start_external_services(mcp=False)

    bot = WebBot()
    scheduler = setup_scheduler(bot, str(DB_PATH))
    scheduler.start()
    logger.info("Scheduler started")

    update_check_task = start_update_check()

    try:
        await run_web(bot, str(DB_PATH))
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await stop_runtime_tasks(update_check_task)
        scheduler.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        stop_external_services(mcp=False)
