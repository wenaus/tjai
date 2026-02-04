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

from .ai import get_assistant, set_user_location
from .config import Config
from .conversation import get_conversation_store
from .voice import transcribe_telegram_voice, text_to_speech

logger = logging.getLogger(__name__)

# Voice response mode: if True, always respond with voice; if False, match input type
ALWAYS_VOICE_RESPONSE = True


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


async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str, voice_response: bool = False):
    """Process a message and send response."""
    user_id = update.effective_user.id
    conv = await asyncio.to_thread(get_conversation_store, user_id)
    assistant = get_assistant()

    # Show typing/recording indicator
    action = "record_voice" if voice_response else "typing"
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=action)

    try:
        # Get response from Claude
        history = conv.get_history()
        response = await asyncio.to_thread(assistant.chat, user_text, history)

        # Save to DB
        await asyncio.to_thread(conv.add_user, user_text)
        await asyncio.to_thread(conv.add_assistant, response)

        # Send response (voice if requested or ALWAYS_VOICE_RESPONSE is True)
        if voice_response or ALWAYS_VOICE_RESPONSE:
            audio_path = await asyncio.to_thread(text_to_speech, response)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)
        else:
            await update.message.reply_text(response)

    except Exception as e:
        logger.exception(f"Error processing message: {e}")
        await update.message.reply_text(f"Error: {e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logger.warning(f"Unauthorized user {user_id} attempted access")
        return

    user_text = update.message.text
    logger.info(f"Text from {user_id}: {user_text[:50]}...")
    await process_message(update, context, user_text, voice_response=False)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logger.warning(f"Unauthorized user {user_id} attempted access")
        return

    logger.info(f"Voice from {user_id}")
    voice_file = await update.message.voice.get_file()
    user_text = await transcribe_telegram_voice(voice_file)
    logger.info(f"Transcribed: {user_text[:50]}...")
    await process_message(update, context, user_text, voice_response=True)


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle location updates."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    loc = update.message.location
    set_user_location(loc.latitude, loc.longitude)
    logger.info(f"Location from {user_id}: {loc.latitude}, {loc.longitude}")
    await update.message.reply_text(f"Location updated: {loc.latitude:.4f}, {loc.longitude:.4f}")


def create_application() -> Application:
    """Create and configure the Telegram application."""
    Config.validate()

    application = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))

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
