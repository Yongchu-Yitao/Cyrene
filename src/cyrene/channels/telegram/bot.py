import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from cyrene.config import ASSISTANT_NAME, DATA_DIR, DB_PATH, OWNER_ID, TELEGRAM_BOT_TOKEN
from cyrene.runtime.scheduler import reset_lottery, setup_scheduler
from cyrene.workbench.channel_chat_service import get_channel_chat_service

logger = logging.getLogger(__name__)

_TELEGRAM_MAX_LENGTH = 4096


def _format_pending_question(question: dict) -> str:
    """Format a pending question as a Telegram message."""
    text = str(question.get("text", "")).strip()
    options = question.get("options", []) or []

    if options:
        lines = [text, ""]
        for i, opt in enumerate(options, start=1):
            label = str(opt.get("label", opt) if isinstance(opt, dict) else opt).strip()
            lines.append(f"{i}. {label}")
        if question.get("allow_custom", True):
            lines.append("")
            lines.append("（也可以直接输入您的回答）")
    else:
        lines = [text]

    return "\n".join(lines)


def _is_owner(update: Update) -> bool:
    return OWNER_ID is not None and update.effective_user is not None and update.effective_user.id == OWNER_ID


async def _start(update: Update, context) -> None:
    if not _is_owner(update):
        return
    await update.message.reply_text(
        f"Hi! I'm {ASSISTANT_NAME}, your personal AI assistant. Send me a message to get started.\n\n"
        "Commands:\n"
        "/clear - Reset conversation session"
    )


async def _clear(update: Update, context) -> None:
    if not _is_owner(update):
        return
    if update.effective_chat is None:
        return
    chat_id = update.effective_chat.id
    service = get_channel_chat_service(
        str(DB_PATH),
        channel="telegram",
        identity=str(chat_id),
        bot=context.bot,
        host_chat_id=chat_id,
    )
    await service.clear()
    await update.message.reply_text("Session cleared. Starting fresh!")


async def _send_response(update: Update, bot, chat_id: int, response: str) -> None:
    """Send a response, splitting it if it exceeds Telegram's limit."""
    for i in range(0, len(response), _TELEGRAM_MAX_LENGTH):
        chunk = response[i : i + _TELEGRAM_MAX_LENGTH]
        await update.message.reply_text(chunk)


async def _handle_message(update: Update, context) -> None:
    if not _is_owner(update) or not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text

    # User initiative resets the proactive lottery impulse
    reset_lottery()

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    service = get_channel_chat_service(
        str(DB_PATH),
        channel="telegram",
        identity=str(chat_id),
        bot=context.bot,
        host_chat_id=chat_id,
    )
    try:
        result = await service.turn(user_text)
    except Exception as exc:
        logger.exception("Telegram Agent turn failed")
        await update.message.reply_text(f"处理消息时出错：{exc}")
        return
    if result.awaiting_user and result.pending_question is not None:
        await update.message.reply_text(
            _format_pending_question(result.pending_question)
        )
        return
    await _send_response(update, context.bot, chat_id, result.text)


async def _post_init(application: Application) -> None:
    from fastapi import APIRouter, FastAPI
    from agent.plugin import (
        PluginApplicationHost,
        set_active_plugin_application_host,
    )
    from cyrene.runtime.bootstrap import start_external_services

    plugin_host = PluginApplicationHost.load_user_plugins(
        app=FastAPI(),
        bot=application.bot,
        db_path=str(DB_PATH),
        data_directory=DATA_DIR,
    )
    plugin_host.attach(APIRouter())
    set_active_plugin_application_host(plugin_host)
    await plugin_host.startup()
    application.bot_data["plugin_application_host"] = plugin_host
    await start_external_services(search=False, mcp=False)
    scheduler = setup_scheduler(application.bot, str(DB_PATH))
    scheduler.start()
    logger.info("Scheduler started")


async def _post_shutdown(application: Application) -> None:
    from agent.plugin import set_active_plugin_application_host
    from cyrene.runtime.bootstrap import stop_external_services_async

    await stop_external_services_async(
        search=False,
        mcp=False,
    )
    plugin_host = application.bot_data.pop("plugin_application_host", None)
    if plugin_host is not None:
        await plugin_host.shutdown()
    set_active_plugin_application_host(None)


def setup_bot() -> Application:
    if not TELEGRAM_BOT_TOKEN or OWNER_ID is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and OWNER_ID must be set to run the Telegram bot.")
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("clear", _clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    return app
