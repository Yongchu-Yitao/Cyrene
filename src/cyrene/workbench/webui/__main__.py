"""Workbench Web UI entry point — python -m cyrene.workbench.webui."""

import asyncio
import logging

from cyrene.config import (
    DB_PATH,
)
from cyrene.observability.logging_setup import setup_persistent_logging
from cyrene.runtime.bootstrap import (
    initialize_runtime,
    start_update_check,
    stop_runtime_tasks,
)
from cyrene.workbench.webui.server import run_web, WebBot

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
setup_persistent_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    await initialize_runtime(
        events=True,
        include_temp=True,
        clean_temp=True,
    )
    bot = WebBot()

    update_check_task = start_update_check()

    try:
        await run_web(bot, str(DB_PATH))
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await stop_runtime_tasks(update_check_task)


if __name__ == "__main__":
    asyncio.run(main())
