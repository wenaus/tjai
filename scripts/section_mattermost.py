#!/usr/bin/env python3
"""Appends ## Mattermost Bots to the daily synopsis entry."""
import logging
import re
from collections import defaultdict
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError
import json

import bootstrap  # noqa: F401 - Django setup
from tjai_app.tjai_utils import get_app_tz
from synopsis_utils import main_section

SWF_MONITOR_API = 'https://pandaserver02.sdcc.bnl.gov/swf-monitor/api/ai-memory/'
MM_PERMALINK = 'https://chat.epic-eic.org/main/pl/'
BOTS = [
    ('pandabot', 'PanDA / Production'),
    ('testbedbot', 'Streaming Workflow Testbed'),
]
FETCH_TURNS = 100  # generous; filter to 24h client-side

logger = logging.getLogger('section_mattermost')


def _fetch_bot_messages(bot_username, turns=FETCH_TURNS):
    """Fetch recent bot dialog from swf-monitor REST API."""
    url = f'{SWF_MONITOR_API}?username={bot_username}&turns={turns}'
    try:
        req = Request(url, headers={'Accept': 'application/json'})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data.get('items', [])
    except (URLError, json.JSONDecodeError, OSError) as e:
        logger.error("Failed to fetch %s dialog: %s", bot_username, e)
        return []


def _parse_user_prefix(content):
    """Extract (mm_user, context, message) from '[user in #channel] ...' or '[user in DM] ...'."""
    m = re.match(r'\[(\S+)\s+in\s+(\S+)\]\s*', content)
    if m:
        return m.group(1), m.group(2), content[m.end():]
    return None, None, content


def _is_channel_message(msg):
    """True if message is from a bot channel (not a DM)."""
    content = msg.get('content', '')
    m = re.match(r'\[(\S+)\s+in\s+(\S+)\]', content)
    if m:
        context = m.group(2)
        return context.startswith('#')
    # Unprefixed messages (old data before tagging) — assume channel
    return True


def _format_bot_section(bot_username, bot_label, messages, since_ts):
    """Format one bot's activity. Only channel top-level posts, with thread reply counts."""
    # Filter to time window and channel messages only
    filtered = []
    for msg in messages:
        ts_str = msg.get('created_at')
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.timestamp() < since_ts:
                continue
        except (ValueError, TypeError):
            continue
        if not _is_channel_message(msg):
            continue
        filtered.append((ts, msg))

    if not filtered:
        return [], 0

    # Count thread replies per root_id (user messages only)
    thread_replies = defaultdict(int)
    for ts, msg in filtered:
        if msg.get('role') != 'user':
            continue
        root_id = msg.get('root_id', '')
        if root_id:
            thread_replies[root_id] += 1

    # Top-level user messages: root_id empty
    top_level = []
    for ts, msg in filtered:
        if msg.get('role') != 'user':
            continue
        if not msg.get('root_id', ''):
            top_level.append((ts, msg))

    if not top_level:
        return [], 0

    lines = [f'**{bot_label}** ({len(top_level)} topic{"s" if len(top_level) != 1 else ""})']

    for ts, msg in top_level:
        content = msg['content']
        mm_user, context, text = _parse_user_prefix(content)
        post_id = msg.get('post_id', '')

        text = text.strip()
        if len(text) > 120:
            text = text[:117] + '...'

        date_str = ts.astimezone(get_app_tz()).strftime('%m/%d %H:%M')
        user_tag = f' ({mm_user})' if mm_user else ''

        # Thread followups: this post's post_id is the root_id for replies
        replies = thread_replies.get(post_id, 0) if post_id else 0
        reply_tag = f' [{replies} {"reply" if replies == 1 else "replies"}]' if replies else ''

        if post_id:
            link = f'<a href="{MM_PERMALINK}{post_id}" target="_blank">{text}</a>'
            lines.append(f'- {date_str}{user_tag} — {link}{reply_tag}')
        else:
            lines.append(f'- {date_str}{user_tag} — {text}{reply_tag}')

    return lines, len(top_level)


def build(since_ts, target_date):
    """Return markdown body or None."""
    all_lines = []

    for bot_username, bot_label in BOTS:
        messages = _fetch_bot_messages(bot_username)
        lines, count = _format_bot_section(bot_username, bot_label, messages, since_ts)
        if lines:
            all_lines.extend(lines)
            all_lines.append('')

    if not all_lines:
        return None

    return '\n'.join(all_lines).rstrip()


if __name__ == '__main__':
    main_section('Mattermost Bots', build)
