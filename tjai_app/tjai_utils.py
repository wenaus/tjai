"""
tjai shared utilities — server-side datetime formatting.

All datetime output is timezone-aware, 24-hour clock, Eastern time by default
(from sysconfig 'timezone' key). Dashboard is the reference for format conventions.

Canonical formats:
  fmt_datetime: "Tue 03/04/14:32" (current year), "03/04/2026" (other year)
  fmt_date:     "Tue Mar 4" (calendar-style)
  fmt_time:     "14:32"
  fmt_duration: "45s", "12m", "3h 15m", "2d 5h"
  fmt_ago:      "5m ago", "3h 15m ago"
"""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

_DEFAULT_TZ = ZoneInfo('America/New_York')

# Cached timezone — refreshed every 5 minutes
_tz_cache = {'tz': None, 'expires': 0}


def get_app_tz():
    """Get app timezone from sysconfig, cached 5 minutes."""
    now = time.time()
    if _tz_cache['tz'] and now < _tz_cache['expires']:
        return _tz_cache['tz']
    try:
        from .models import SysConfig
        cfg = SysConfig.objects.filter(key='timezone').first()
        tz = ZoneInfo(cfg.value) if cfg else _DEFAULT_TZ
    except Exception:
        tz = _DEFAULT_TZ
    _tz_cache['tz'] = tz
    _tz_cache['expires'] = now + 300
    return tz


def _to_dt(ts, tz=None):
    """Convert epoch seconds, datetime, or ISO string to tz-aware datetime."""
    if ts is None:
        return None
    if tz is None:
        tz = get_app_tz()
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=tz)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
            return dt.astimezone(tz)
        except (ValueError, TypeError):
            return None
    if isinstance(ts, datetime):
        return ts.astimezone(tz) if ts.tzinfo else ts.replace(tzinfo=tz)
    return None


def fmt_datetime(ts, tz=None):
    """
    Format timestamp as 'Tue 03/04/14:32' (current year) or '03/04/2026' (other year).
    """
    dt = _to_dt(ts, tz)
    if not dt:
        return ''
    if dt.year != datetime.now(tz=dt.tzinfo).year:
        return dt.strftime('%m/%d/%Y')
    return dt.strftime('%a %m/%d/%H:%M')


def fmt_date(ts, tz=None):
    """Format as 'Tue Mar 4' (calendar-style date, no time)."""
    dt = _to_dt(ts, tz)
    if not dt:
        return ''
    return dt.strftime('%a %b %-d')


def fmt_time(ts, tz=None):
    """Format as '14:32' (24-hour time only)."""
    dt = _to_dt(ts, tz)
    if not dt:
        return ''
    return dt.strftime('%H:%M')


def fmt_duration(seconds):
    """Format duration: 45s, 12m, 3h 15m, 2d 5h."""
    if seconds is None:
        return ''
    seconds = abs(int(seconds))
    if seconds < 60:
        return f'{seconds}s'
    if seconds < 3600:
        return f'{seconds // 60}m'
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f'{h}h {m}m' if m else f'{h}h'
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f'{d}d {h}h' if h else f'{d}d'


def fmt_ago(ts, tz=None):
    """Format as relative time: '5m ago', '3h 15m ago'."""
    dt = _to_dt(ts, tz)
    if not dt:
        return ''
    now = datetime.now(tz=dt.tzinfo)
    seconds = int((now - dt).total_seconds())
    if seconds < 0:
        seconds = 0
    return fmt_duration(seconds) + ' ago'
