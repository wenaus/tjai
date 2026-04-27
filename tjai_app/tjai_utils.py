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

import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

_DEFAULT_TZ = ZoneInfo('America/New_York')
_VOID_HTML_TAGS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr',
}

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


def safe_truncate(text, max_chars, suffix='...'):
    """Truncate text without leaving broken HTML/XML-style markup.

    The returned string is at most max_chars including suffix. If the cut
    point would land between '<' and '>', or inside an unclosed element, back
    up to before that tag so diagnostics cannot emit fragments like '</hea'
    or leave a raw '<title>' region open.
    """
    if text is None:
        return ''
    text = str(text)
    if max_chars is None or len(text) <= max_chars:
        return text
    max_chars = int(max_chars)
    if max_chars <= 0:
        return ''

    suffix = str(suffix)
    if len(suffix) >= max_chars:
        return suffix[:max_chars]

    cut = max_chars - len(suffix)
    safe_cut = _safe_tag_cut_point(text, cut)
    return text[:safe_cut].rstrip() + suffix


def _safe_tag_cut_point(text, cut):
    """Return a cut point that does not split or leave open tags."""
    while True:
        adjusted = _back_out_of_partial_tag(text, cut)
        adjusted = _back_out_of_unclosed_tag(text, adjusted)
        if adjusted == cut:
            return cut
        cut = adjusted


def _back_out_of_partial_tag(text, cut):
    last_lt = text.rfind('<', 0, cut)
    last_gt = text.rfind('>', 0, cut)
    if last_lt <= last_gt or not _is_markup_start(text, last_lt):
        return cut
    return last_lt


def _back_out_of_unclosed_tag(text, cut):
    stack = []
    tag_re = re.compile(r'<(/?)([A-Za-z][A-Za-z0-9:-]*)(?:\s[^>]*)?(/?)>')
    for match in tag_re.finditer(text[:cut]):
        tag = match.group(2).lower()
        if tag in _VOID_HTML_TAGS or match.group(3):
            continue
        if match.group(1):
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == tag:
                    del stack[i:]
                    break
        else:
            stack.append((tag, match.start()))
    if not stack:
        return cut
    return stack[-1][1]


def _is_markup_start(text, lt_index):
    if lt_index < 0:
        return False
    next_char_idx = lt_index + 1
    if next_char_idx >= len(text):
        return True
    next_char = text[next_char_idx]
    return next_char.isalpha() or next_char in '/!?'
