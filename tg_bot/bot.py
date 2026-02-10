"""Telegram bot handlers."""

import asyncio
import logging
import time
import uuid

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
from .reminders import check_reminders
from .voice import transcribe_telegram_voice, text_to_speech

logger = logging.getLogger(__name__)

# Voice mode preference
_voice_mode_cache: bool | None = None
DEFAULT_VOICE_MODE = True

# Last response cache for "repeat that"
_last_response: dict[int, str] = {}  # user_id -> last response text


def get_voice_mode() -> bool:
    """Get voice mode preference (True=voice, False=text)."""
    global _voice_mode_cache
    if _voice_mode_cache is not None:
        return _voice_mode_cache

    from tjai_app.models import SysConfig
    config = SysConfig.objects.filter(key='tg_voice_mode').first()
    if config:
        _voice_mode_cache = config.value == 'voice'
    else:
        _voice_mode_cache = DEFAULT_VOICE_MODE
    return _voice_mode_cache


def set_voice_mode(voice: bool):
    """Set voice mode preference."""
    global _voice_mode_cache
    from tjai_app.models import SysConfig

    _voice_mode_cache = voice
    SysConfig.objects.update_or_create(
        key='tg_voice_mode',
        defaults={
            'value': 'voice' if voice else 'text',
            'timestamp_modified': time.time(),
        }
    )
    logger.info(f"Voice mode set to {'voice' if voice else 'text'}")


def check_trigger(text: str) -> str | None:
    """Check for voice command triggers."""
    lower = text.lower().strip().rstrip('.')
    triggers = {
        'use voice': 'voice',
        'use text': 'text',
        'clear history': 'clear',
        'repeat that': 'repeat',
        'save that': 'save',
        'voice help': 'help',
    }
    return triggers.get(lower)


VOICE_HELP_TEXT = """Voice commands:
- use voice: switch to voice responses
- use text: switch to text responses
- clear history: start fresh conversation
- repeat that: repeat last response
- save that: save last response to memory
- voice help: show this help"""


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


async def voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /voice command to switch to voice mode."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Unauthorized.")
        return
    await asyncio.to_thread(set_voice_mode, True)
    await update.message.reply_text("Switched to voice mode.")


async def text_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /text command to switch to text mode."""
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("Unauthorized.")
        return
    await asyncio.to_thread(set_voice_mode, False)
    await update.message.reply_text("Switched to text mode.")


async def handle_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE, trigger: str, use_voice: bool):
    """Handle a voice command trigger."""
    user_id = update.effective_user.id

    if trigger == 'voice':
        await asyncio.to_thread(set_voice_mode, True)
        msg = "Switched to voice mode."
        await update.message.reply_text(msg)
        if use_voice:
            audio_path = await asyncio.to_thread(text_to_speech, msg)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)

    elif trigger == 'text':
        await asyncio.to_thread(set_voice_mode, False)
        await update.message.reply_text("Switched to text mode.")

    elif trigger == 'clear':
        conv = await asyncio.to_thread(get_conversation_store, user_id)
        conv.clear()
        await asyncio.to_thread(get_assistant().refresh_context)
        msg = "Conversation cleared."
        await update.message.reply_text(msg)
        if use_voice and get_voice_mode():
            audio_path = await asyncio.to_thread(text_to_speech, msg)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)

    elif trigger == 'repeat':
        last = _last_response.get(user_id)
        if not last:
            await update.message.reply_text("Nothing to repeat.")
            return
        await update.message.reply_text(last)
        if use_voice or get_voice_mode():
            audio_path = await asyncio.to_thread(text_to_speech, last)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)

    elif trigger == 'save':
        last = _last_response.get(user_id)
        if not last:
            await update.message.reply_text("Nothing to save.")
            return
        await asyncio.to_thread(_save_to_memory, last, user_id)
        msg = "Response saved to memory."
        await update.message.reply_text(msg)
        if use_voice and get_voice_mode():
            audio_path = await asyncio.to_thread(text_to_speech, msg)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)

    elif trigger == 'help':
        await update.message.reply_text(VOICE_HELP_TEXT)
        if use_voice and get_voice_mode():
            audio_path = await asyncio.to_thread(text_to_speech, VOICE_HELP_TEXT)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)


def _save_to_memory(content: str, user_id: int):
    """Save content to tjai memory."""
    from tjai_app.models import Entry, Context, Tag

    now = time.time()
    context, _ = Context.objects.get_or_create(
        name='tgbot',
        defaults={
            'title': 'Telegram Bot',
            'timestamp_created': now,
            'timestamp_modified': now,
        }
    )

    entry = Entry.objects.create(
        id=str(uuid.uuid4()),
        content=content,
        kind='memory',
        context=context,
        data={'telegram_user_id': user_id, 'saved_via': 'voice_command'},
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )
    Tag.objects.create(tag_name='fromai', entry=entry)
    Tag.objects.create(tag_name='fromtg', entry=entry)
    logger.info(f"Saved response to memory: {entry.id}")


async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str, from_voice: bool = False):
    """Process a message and send response."""
    user_id = update.effective_user.id
    conv = await asyncio.to_thread(get_conversation_store, user_id)
    assistant = get_assistant()

    # Determine response mode
    voice_response = get_voice_mode()

    # Show typing/recording indicator
    action = "record_voice" if voice_response else "typing"
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=action)

    try:
        # Get response from Claude
        history = conv.get_history()
        response = await asyncio.to_thread(assistant.chat, user_text, history)

        # Cache for "repeat that" / "save that"
        _last_response[user_id] = response

        # Save to DB
        await asyncio.to_thread(conv.add_user, user_text)
        await asyncio.to_thread(conv.add_assistant, response)

        # Send response based on mode
        if voice_response:
            # Voice mode: send text first, then voice
            await update.message.reply_text(response)
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

    # Check for voice command triggers
    trigger = check_trigger(user_text)
    if trigger:
        await handle_trigger(update, context, trigger, use_voice=False)
        return

    await process_message(update, context, user_text, from_voice=False)


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

    # Check for voice command triggers
    trigger = check_trigger(user_text)
    if trigger:
        await handle_trigger(update, context, trigger, use_voice=True)
        return

    await process_message(update, context, user_text, from_voice=True)


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
    application.add_handler(CommandHandler("voice", voice_command))
    application.add_handler(CommandHandler("text", text_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))

    # Calendar reminders: check every 5 minutes, start after 10 seconds
    application.job_queue.run_repeating(check_reminders, interval=300, first=10)

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
