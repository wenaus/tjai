"""Calendar event reminders via Telegram push notifications."""

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from tjai_app.models import Entry, SysConfig
from .config import Config

logger = logging.getLogger(__name__)

# Entry IDs already notified (in-memory, resets on bot restart)
_notified = set()

REMINDER_WINDOW_SECONDS = 900  # 15 minutes


def _get_upcoming_events():
    """Get upcoming events within reminder window (sync, for use in thread)."""
    tz_config = SysConfig.objects.filter(key='timezone').first()
    tz_name = tz_config.value if tz_config else 'America/New_York'
    tz = ZoneInfo(tz_name)

    now_ts = time.time()
    window_ts = now_ts + REMINDER_WINDOW_SECONDS

    results = []
    for entry in Entry.objects.filter(kind='journal', deleted_at__isnull=True):
        if not entry.data or not isinstance(entry.data, dict):
            continue
        event_date = entry.data.get('event_date')
        if event_date is None or not isinstance(event_date, (int, float)):
            continue
        if not (now_ts <= event_date < window_ts):
            continue
        if entry.id in _notified:
            continue

        event_dt = datetime.fromtimestamp(event_date, tz=tz)
        results.append({
            'id': entry.id,
            'content': entry.content,
            'time': event_dt.strftime('%H:%M'),
        })

    return results


async def check_reminders(context):
    """Check for upcoming calendar events and send reminders."""
    events = await asyncio.to_thread(_get_upcoming_events)

    for event in events:
        msg = f"Reminder: {event['content']} ({event['time']})"
        try:
            await context.bot.send_message(
                chat_id=Config.TELEGRAM_USER_ID,
                text=msg,
            )
            _notified.add(event['id'])
            logger.info(f"Sent reminder: {event['content']} at {event['time']}")
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")
