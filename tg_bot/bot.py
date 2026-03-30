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


def _write_heartbeat():
    """Write bot heartbeat timestamp to SysConfig."""
    from tjai_app.models import SysConfig
    SysConfig.objects.update_or_create(
        key='tg_bot_heartbeat',
        defaults={'value': str(time.time()), 'timestamp_modified': time.time()}
    )


async def heartbeat_job(context):
    """Periodic heartbeat update for remote health checks."""
    await asyncio.to_thread(_write_heartbeat)


def check_trigger(text: str) -> str | None:
    """Check for voice command triggers.

    Returns trigger name, or 'get:name' for get commands.
    Robust against punctuation from voice transcription.
    """
    import re

    lower = text.lower().strip().rstrip('.')
    triggers = {
        'use voice': 'voice',
        'use text': 'text',
        'clear history': 'clear',
        'repeat that': 'repeat',
        'save that': 'save',
        'voice help': 'help',
    }
    if lower in triggers:
        return triggers[lower]

    # Check for "calendar" command (with optional punctuation)
    if re.match(r'^calendar[.,!?]?$', lower):
        return 'calendar'

    # Check for "recent" command (with optional punctuation)
    if re.match(r'^recent[.,!?]?$', lower):
        return 'recent'

    # Commands with arguments - robust against "cmd, arg" or "cmd: arg" etc.
    # Pattern: command word, optional punctuation, then content
    def extract_args(cmd: str, text: str) -> str | None:
        """Extract args after command, handling punctuation."""
        pattern = rf'^{cmd}[,.:;!\s]+(.+)$'
        match = re.match(pattern, text.strip(), re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    def strip_trailing_punct(s: str) -> str:
        """Strip trailing punctuation from a name token (voice transcription artifact)."""
        return s.rstrip(',.:;!')

    # Check for "get <name>" command
    args = extract_args('get', text)
    if args:
        return f'get:{strip_trailing_punct(args).lower()}'

    # Check for "memo <text>" command - preserve case
    args = extract_args('memo', text)
    if args:
        return f'memo:{args}'

    # Check for "journal <datetime> <text>" command - preserve case
    args = extract_args('journal', text)
    if args:
        return f'journal:{args}'

    # Check for "add <name> <text>" command - append to named entry
    # Format: add <name> <text> - name is first word, rest is text
    args = extract_args('add', text)
    if args:
        parts = args.split(None, 1)  # split into name and rest
        if len(parts) == 2:
            name, content = parts
            return f'add:{strip_trailing_punct(name).lower()}:{content}'

    return None


def prepare_for_voice(text: str) -> str:
    """Prepare text for voice output - extract titles from markdown links, etc."""
    import re
    # Replace [Title](URL) with just Title
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    return text


VOICE_HELP_TEXT = """Voice commands:
- use voice: switch to voice responses
- use text: switch to text responses
- clear history: start fresh conversation
- repeat that: repeat last response
- save that: save last response to memory
- get <name>: retrieve named entry
- add <name> <text>: append to named entry
- memo <text>: save text as memory
- calendar: today and tomorrow's events
- journal <datetime> <text>: create calendar event
- recent: last 5 entries
- voice help: show this help"""


def get_entry_by_name(name: str) -> str | None:
    """Look up a tjai entry by name (case-insensitive)."""
    from tjai_app.models import Entry

    entry = Entry.objects.filter(
        name__iexact=name,
        deleted_at__isnull=True
    ).first()

    if entry:
        return entry.content
    return None


def append_to_entry(name: str, text: str) -> tuple[str, str] | None:
    """Append text to a named entry. Returns (text_msg, voice_msg) or None if not found."""
    from tjai_app.models import Entry

    entry = Entry.objects.filter(
        name__iexact=name,
        deleted_at__isnull=True
    ).first()

    if not entry:
        return None

    # Append with newline
    entry.content = entry.content.rstrip() + '\n' + text.strip()
    entry.timestamp_modified = time.time()
    entry.is_dirty = 1
    entry.save(update_fields=['content', 'timestamp_modified', 'is_dirty'])

    msg = f"Added to {name}: {text.strip()}"
    return msg, msg


def create_memo(content: str, user_id: int) -> str:
    """Create a memory entry, return confirmation message."""
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
        id=str(uuid.uuid7()),
        content=content,
        kind='memory',
        context=context,
        data={'telegram_user_id': user_id, 'saved_via': 'memo_command'},
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )
    Tag.objects.create(tag_name='fromai', entry=entry)
    Tag.objects.create(tag_name='fromtg', entry=entry)
    logger.info(f"Created memo: {entry.id}")
    return "Saved."


def get_calendar_summary() -> tuple[str, str]:
    """Get today and tomorrow's calendar events.

    Returns (content, tz_abbrev) tuple.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from tjai_app.models import Entry

    # Use Eastern time (user's work timezone)
    tz_name = 'America/New_York'
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = None

    now = datetime.now(tz) if tz else datetime.now()
    tz_abbrev = now.strftime('%Z') if tz else "local"

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_end = today_start + timedelta(days=2)
    start_ts = today_start.timestamp()
    end_ts = tomorrow_end.timestamp()

    # Query journal entries - event_date is in data JSON field
    entries = Entry.objects.filter(
        kind='journal',
        deleted_at__isnull=True,
    )

    # Filter by event_date in data field and sort
    matching = []
    for entry in entries:
        if not entry.data or not isinstance(entry.data, dict):
            continue
        event_ts = entry.data.get('event_date')
        if event_ts is None or not isinstance(event_ts, (int, float)):
            continue
        if start_ts <= event_ts < end_ts:
            matching.append((event_ts, entry))

    if not matching:
        return "No events today or tomorrow.", tz_abbrev

    matching.sort(key=lambda x: x[0])

    lines = []
    current_day = None
    for event_ts, entry in matching:
        event_dt = datetime.fromtimestamp(event_ts, tz) if tz else datetime.fromtimestamp(event_ts)
        day_label = "Today" if event_dt.date() == now.date() else "Tomorrow"

        if day_label != current_day:
            if lines:
                lines.append("")
            lines.append(f"{day_label}:")
            current_day = day_label

        time_str = event_dt.strftime('%I:%M%p').lower().lstrip('0')
        lines.append(f"  {time_str} {entry.content}")

    return "\n".join(lines), tz_abbrev


def get_recent_entries() -> tuple[str, str]:
    """Get last 5 entries, oldest first.

    Returns (text_output, voice_output) tuple.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from tjai_app.models import Entry

    tz = ZoneInfo('America/New_York')

    # Get last 5 entries by modification time, then reverse for oldest first
    entries = list(Entry.objects.filter(
        deleted_at__isnull=True
    ).order_by('-timestamp_modified')[:5])
    entries.reverse()  # oldest first

    if not entries:
        msg = "No recent entries."
        return msg, msg

    text_lines = []
    voice_lines = []

    for entry in entries:
        # Get first line of content as title
        title = entry.content.split('\n')[0].strip()
        if len(title) > 80:
            title = title[:77] + "..."

        # Format datetime
        dt = datetime.fromtimestamp(entry.timestamp_modified, tz)
        # Text: full time with minutes
        text_time = dt.strftime('%m/%d ') + dt.strftime('%I:%M%p').lower().lstrip('0')
        # Voice: hour only, no minutes
        hour = dt.strftime('%I').lstrip('0')
        ampm = dt.strftime('%p').lower()
        voice_time = dt.strftime('%B %d ').replace(' 0', ' ') + f"{hour} {ampm}"

        # Context and tags for text only (=context :tag format)
        ctx = f" ={entry.context.name}" if entry.context else ""
        entry_tags = list(entry.tags.all())
        tags = ""
        if entry_tags:
            tags = " " + " ".join(f":{t.tag_name}" for t in entry_tags[:3])

        text_lines.append(f"{text_time}{ctx}{tags} {title}")
        voice_lines.append(f"{voice_time} {title}")

    text_output = "\n".join(text_lines)
    voice_output = "\n".join(voice_lines)

    return text_output, voice_output


def create_journal_entry(args: str, user_id: int) -> tuple[str, str]:
    """Create a journal entry from voice command args.

    Returns (text_msg, voice_msg) tuple.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from tjai_app.models import Entry, Tag
    import re

    tz = ZoneInfo('America/New_York')
    now_dt = datetime.now(tz)

    # Simple date/time parsing for voice commands
    args_lower = args.lower().strip()
    event_dt = None
    content = args

    # Check for "tomorrow" or "today"
    if args_lower.startswith('tomorrow '):
        base_date = now_dt + timedelta(days=1)
        rest = args[9:].strip()
    elif args_lower.startswith('today '):
        base_date = now_dt
        rest = args[6:].strip()
    else:
        base_date = now_dt
        rest = args

    # Parse time like "2pm", "2:30pm", "14:00"
    time_match = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s+(.+)$', rest, re.IGNORECASE)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        ampm = (time_match.group(3) or '').lower()
        content = time_match.group(4)

        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0

        event_dt = base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        # No time specified, use noon
        event_dt = base_date.replace(hour=12, minute=0, second=0, microsecond=0)
        content = rest

    if not content.strip():
        err = "No event description provided."
        return err, err

    timestamp = event_dt.timestamp()
    now = time.time()

    entry = Entry.objects.create(
        id=str(uuid.uuid7()),
        content=content.strip(),
        kind='journal',
        data={'event_date': timestamp, 'telegram_user_id': user_id, 'saved_via': 'journal_command'},
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )
    Tag.objects.create(tag_name='fromai', entry=entry)
    Tag.objects.create(tag_name='fromtg', entry=entry)

    time_str = event_dt.strftime('%m/%d %I:%M%p').lower()
    # Voice-friendly: "February 11 2pm" (no punctuation)
    voice_time = event_dt.strftime('%B %-d %-I%p').lower()
    logger.info(f"Created journal: {entry.id} at {time_str}")
    text_msg = f"Added {time_str}: {content.strip()}"
    voice_msg = f"Added {voice_time} {content.strip()}"
    return text_msg, voice_msg


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

    try:
        await _handle_trigger_impl(update, context, trigger, use_voice, user_id)
    except Exception as e:
        logger.exception(f"Error handling trigger '{trigger}': {e}")
        await update.message.reply_text(f"Error: {e}")


async def _handle_trigger_impl(update: Update, context: ContextTypes.DEFAULT_TYPE, trigger: str, use_voice: bool, user_id: int):
    """Implementation of trigger handling."""
    if trigger == 'voice':
        await asyncio.to_thread(set_voice_mode, True)
        msg = "Switched to voice mode."
        await update.message.reply_text(msg)
        # Now in voice mode, so send voice confirmation
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
        if await asyncio.to_thread(get_voice_mode):
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
        if await asyncio.to_thread(get_voice_mode):
            voice_text = prepare_for_voice(last)
            audio_path = await asyncio.to_thread(text_to_speech, voice_text)
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
        if await asyncio.to_thread(get_voice_mode):
            audio_path = await asyncio.to_thread(text_to_speech, msg)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)

    elif trigger.startswith('get:'):
        name = trigger[4:]
        content = await asyncio.to_thread(get_entry_by_name, name)
        if not content:
            msg = f"No entry named {name}."
            await update.message.reply_text(msg)
            if await asyncio.to_thread(get_voice_mode):
                audio_path = await asyncio.to_thread(text_to_speech, msg)
                try:
                    await update.message.reply_voice(voice=open(audio_path, "rb"))
                finally:
                    audio_path.unlink(missing_ok=True)
            return
        # Cache for repeat/save
        _last_response[user_id] = content
        await update.message.reply_text(content)
        if await asyncio.to_thread(get_voice_mode):
            voice_text = prepare_for_voice(content)
            audio_path = await asyncio.to_thread(text_to_speech, voice_text)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)

    elif trigger.startswith('memo:'):
        content = trigger[5:]
        msg = await asyncio.to_thread(create_memo, content, user_id)
        await update.message.reply_text(msg)
        if await asyncio.to_thread(get_voice_mode):
            audio_path = await asyncio.to_thread(text_to_speech, msg)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)

    elif trigger.startswith('add:'):
        # Format: add:name:content
        parts = trigger[4:].split(':', 1)
        if len(parts) == 2:
            name, content = parts
            result = await asyncio.to_thread(append_to_entry, name, content)
            if result:
                text_msg, voice_msg = result
                await update.message.reply_text(text_msg)
                if await asyncio.to_thread(get_voice_mode):
                    audio_path = await asyncio.to_thread(text_to_speech, voice_msg)
                    try:
                        await update.message.reply_voice(voice=open(audio_path, "rb"))
                    finally:
                        audio_path.unlink(missing_ok=True)
            else:
                # Entry not found - treat as normal message to LLM
                original_text = update.message.text
                await process_message(update, context, original_text, from_voice=use_voice)

    elif trigger == 'calendar':
        content, tz_abbrev = await asyncio.to_thread(get_calendar_summary)
        _last_response[user_id] = content
        await update.message.reply_text(content)
        if await asyncio.to_thread(get_voice_mode):
            voice_text = prepare_for_voice(content) + f"\n\nAll times {tz_abbrev}."
            audio_path = await asyncio.to_thread(text_to_speech, voice_text)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)

    elif trigger == 'recent':
        text_content, voice_content = await asyncio.to_thread(get_recent_entries)
        _last_response[user_id] = text_content
        await update.message.reply_text(text_content)
        if await asyncio.to_thread(get_voice_mode):
            audio_path = await asyncio.to_thread(text_to_speech, voice_content)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)

    elif trigger.startswith('journal:'):
        args = trigger[8:]
        text_msg, voice_msg = await asyncio.to_thread(create_journal_entry, args, user_id)
        await update.message.reply_text(text_msg)
        if await asyncio.to_thread(get_voice_mode):
            audio_path = await asyncio.to_thread(text_to_speech, voice_msg)
            try:
                await update.message.reply_voice(voice=open(audio_path, "rb"))
            finally:
                audio_path.unlink(missing_ok=True)

    elif trigger == 'help':
        # voice help always triggers voice mode and sends voice
        await asyncio.to_thread(set_voice_mode, True)
        await update.message.reply_text(VOICE_HELP_TEXT)
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
        id=str(uuid.uuid7()),
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
    voice_response = await asyncio.to_thread(get_voice_mode)

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

    try:
        user_text = update.message.text
        logger.info(f"Text from {user_id}: {user_text[:50]}...")

        # Check for voice command triggers
        trigger = check_trigger(user_text)
        if trigger:
            await handle_trigger(update, context, trigger, use_voice=False)
            return

        await process_message(update, context, user_text, from_voice=False)
    except Exception as e:
        logger.exception(f"Error in handle_text: {e}")
        await update.message.reply_text(f"Error: {e}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logger.warning(f"Unauthorized user {user_id} attempted access")
        return

    try:
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
    except Exception as e:
        logger.exception(f"Error in handle_voice: {e}")
        await update.message.reply_text(f"Error: {e}")


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle location updates (including live location edits)."""
    msg = update.effective_message
    if not msg or not msg.location:
        return

    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    try:
        loc = msg.location
        set_user_location(loc.latitude, loc.longitude)
        logger.info(f"Location from {user_id}: {loc.latitude}, {loc.longitude}")
        # Only reply on new messages, not live location updates
        if update.message:
            await msg.reply_text(f"Location updated: {loc.latitude:.4f}, {loc.longitude:.4f}")
    except Exception as e:
        logger.exception(f"Error in handle_location: {e}")
        if update.message:
            await msg.reply_text(f"Error: {e}")


async def _post_init(application: Application):
    """Set the menu button to open the Mini App."""
    from telegram import MenuButtonWebApp, WebAppInfo
    try:
        await application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="tjai",
                web_app=WebAppInfo(url="https://etaverse.com/tjai/m/")
            )
        )
        logger.info("Menu button set to Mini App")
    except Exception as e:
        logger.error(f"Failed to set menu button: {e}")


def create_application() -> Application:
    """Create and configure the Telegram application."""
    Config.validate()

    application = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).post_init(_post_init).build()

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

    # Bot heartbeat: update every 60 seconds for remote health checks
    application.job_queue.run_repeating(heartbeat_job, interval=60, first=5)

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
