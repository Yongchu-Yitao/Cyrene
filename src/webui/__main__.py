"""Web UI entry point — python -m cyrene.webui"""

import asyncio
import logging

from cyrene.config import (
    DB_PATH,
)
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
