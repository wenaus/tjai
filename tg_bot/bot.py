"""Telegram bot handlers."""

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .ai import get_assistant
from .config import Config
from .conversation import get_conversation_store

logger = logging.getLogger(__name__)


def is_authorized(user_id: int) -> bool:
    """Check if user is authorized."""
    allowed_id = Config.user_id_int()
    return allowed_id is not None and user_id == allowed_id


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Unauthorized.")
        return

    await update.message.reply_text(
        "tjai assistant ready. Send me a message or voice note."
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clear command to reset conversation."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Unauthorized.")
        return

    conv = await asyncio.to_thread(get_conversation_store, update.effective_user.id)
    conv.clear()
    await asyncio.to_thread(get_assistant().refresh_context)
    await update.message.reply_text("Conversation cleared.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logger.warning(f"Unauthorized user {user_id} attempted access")
        return

    user_text = update.message.text
    logger.info(f"Message from {user_id}: {user_text[:50]}...")

    conv = await asyncio.to_thread(get_conversation_store, user_id)
    assistant = get_assistant()

    # Show typing indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # Get response from Claude (run in thread to avoid async ORM issues)
        history = conv.get_history()
        response = await asyncio.to_thread(assistant.chat, user_text, history)

        # Save to DB (run in thread for ORM)
        await asyncio.to_thread(conv.add_user, user_text)
        await asyncio.to_thread(conv.add_assistant, response)

        # Send response
        await update.message.reply_text(response)

    except Exception as e:
        logger.exception(f"Error processing message: {e}")
        await update.message.reply_text(f"Error: {e}")


def create_application() -> Application:
    """Create and configure the Telegram application."""
    Config.validate()

    application = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return application


def run_bot():
    """Run the bot with long-polling."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    # Reduce noise from httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)

    logger.info("Starting tjai Telegram bot...")
    app = create_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES)
