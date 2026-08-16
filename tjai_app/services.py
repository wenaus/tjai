"""Shared business logic for tjai entry management.

All functions are synchronous (Django ORM). MCP wraps with sync_to_async.
Returns dicts/lists, not ORM objects.
"""

import base64
import difflib
import json
import re
import time
import uuid
from datetime import datetime, timedelta

from django.db import models, transaction
from django.db.models import Count
from django.utils import timezone

from django.db.models import Q

from .dialog_context import DIALOG_CONTEXTS, DIALOG_TAG
from .models import Entry, Context, Tag, SysConfig, Relation, AppLog, Notice
from .tagger import tag_bookmark
from .tjai_utils import fmt_datetime, get_app_tz, safe_truncate
from tj.commands.journal import parse_time
from tj.date_utils import parse_date_filter

VALID_KINDS = ('memory', 'todo', 'journal', 'profile', 'ai', 'bookmark', 'list', 'action', 'goal')
VALID_STATUSES = ('active', 'done', 'blocked', 'archive', 'failed')
DEFAULT_MAX_CONTENT_LENGTH = 500
MAX_RESULT_LIMIT = 500
PICKS_NOOP_SENTINEL = '__noop__'
CAPCOM_SEVERITIES = ('info', 'warning', 'alarm')


def is_noop_pick_bookmark(content, data=None):
    """Return True for the picks agent's no-op sentinel, not a real bookmark."""
    data = data if isinstance(data, dict) else {}
    return (
        (content or '').strip() == PICKS_NOOP_SENTINEL
        or data.get('entry_id') == PICKS_NOOP_SENTINEL
    )


def _validate_result_limit(limit):
    if limit is None:
        return None
    if not isinstance(limit, int) or limit < 1:
        return {"error": f"limit must be a positive integer, got {limit}"}
    if limit > MAX_RESULT_LIMIT:
        return {"error": f"limit must be <= {MAX_RESULT_LIMIT}, got {limit}"}
    return None


def _validate_offset(offset):
    if not isinstance(offset, int) or offset < 0:
        return {"error": f"offset must be a non-negative integer, got {offset}"}
    return None


def _parse_date(date_str):
    """Parse date string to datetime. Supports ISO format and YYYYMMDD.
    Returns (datetime, None) on success, (None, error) on failure, (None, None) if empty.
    """
    if not date_str:
        return None, None
    try:
        if 'T' in date_str or '-' in date_str:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00')), None
        if len(date_str) == 8 and date_str.isdigit():
            return datetime.strptime(date_str, '%Y%m%d'), None
        return None, f"Invalid date format '{date_str}'. Use ISO format or YYYYMMDD."
    except (ValueError, TypeError) as e:
        return None, f"Invalid date '{date_str}': {e}"


def _validate_event_date(date_str):
    """Validate event_date is YYYYMMDD format. Returns error message or None."""
    if not date_str:
        return None
    if not isinstance(date_str, str):
        return f"event_date must be a string, got {type(date_str).__name__}"
    if len(date_str) != 8 or not date_str.isdigit():
        return f"event_date must be YYYYMMDD format, got '{date_str}'"
    try:
        datetime.strptime(date_str, '%Y%m%d')
        return None
    except ValueError:
        return f"Invalid date '{date_str}'"


def _validate_event_time(time_str):
    """Validate event_time is HHMM format. Returns error message or None."""
    if not time_str:
        return None
    if not isinstance(time_str, str):
        return f"event_time must be a string, got {type(time_str).__name__}"
    if len(time_str) != 4 or not time_str.isdigit():
        return f"event_time must be HHMM format, got '{time_str}'"
    hour, minute = int(time_str[:2]), int(time_str[2:])
    if not (0 <= hour <= 23):
        return f"Hour must be 00-23, got {hour:02d}"
    if not (0 <= minute <= 59):
        return f"Minute must be 00-59, got {minute:02d}"
    return None


def _extract_time_from_content(content):
    """Extract time from start of content if present.
    Returns (remaining_content, hour, minute) or (original_content, None, None).
    """
    parts = content.strip().split(None, 1)
    if not parts:
        return content, None, None
    try:
        hour, minute = parse_time(parts[0])
        remaining = parts[1] if len(parts) > 1 else ""
        return remaining.strip(), hour, minute
    except ValueError:
        return content, None, None


def get_timezone():
    """Get configured timezone. Delegates to tjai_utils.get_app_tz() (cached)."""
    return get_app_tz()


def _format_entry(entry, tz=None, max_content_length=None):
    """Format an Entry object for API response."""
    if tz is None:
        tz = get_app_tz()
    content = entry.content
    if max_content_length and len(content) > max_content_length:
        content = content[:max_content_length] + '…'
    result = {
        "id": entry.id,
        "content": content,
        "kind": entry.kind,
        "context": entry.context.name if entry.context else None,
        "created": datetime.fromtimestamp(entry.timestamp_created, tz=tz).isoformat(),
        "modified": datetime.fromtimestamp(entry.timestamp_modified, tz=tz).isoformat(),
        "created_display": fmt_datetime(entry.timestamp_created, tz),
        "modified_display": fmt_datetime(entry.timestamp_modified, tz),
    }
    if entry.name:
        result["name"] = entry.name
    if entry.priority is not None:
        result["priority"] = entry.priority
    if entry.status is not None:
        result["status"] = entry.status
    if entry.data:
        result["data"] = entry.data
    tags = list(entry.tags.values_list('tag_name', flat=True))
    if tags:
        result["tags"] = tags
    return result


def _strip_punct(s):
    """Strip punctuation for lenient content comparison."""
    return re.sub(r'[^\w\s]', '', s)


def _apply_date_filter(qs, start_date, end_date):
    """Apply date range filter to queryset. Both dates optional; None means no filter."""
    tz = get_timezone()
    if start_date is not None:
        start_ts, err = parse_date_filter(start_date, tz=tz)
        if err:
            return None, {"error": err}
        if start_ts:
            qs = qs.filter(timestamp_modified__gte=start_ts)
    if end_date is not None:
        end_ts, err = parse_date_filter(end_date, end_of_day=True, tz=tz)
        if err:
            return None, {"error": err}
        if end_ts:
            qs = qs.filter(timestamp_modified__lte=end_ts)
    return qs, None


def _format_relation(relation):
    """Format a Relation object for API response."""
    result = {
        "id": relation.id,
        "entry1_id": relation.entry1_id,
        "entry2_id": relation.entry2_id,
        "relation_type": relation.relation_type,
        "created": datetime.fromtimestamp(relation.timestamp_created, tz=get_app_tz()).isoformat(),
        "modified": datetime.fromtimestamp(relation.timestamp_modified, tz=get_app_tz()).isoformat(),
    }
    if relation.data:
        result["data"] = relation.data
    return result


def _get_relations_for_entry(entry_id, max_content_length=None):
    """Get all relations touching an entry, with the other entry formatted."""
    rels = Relation.objects.filter(
        Q(entry1_id=entry_id) | Q(entry2_id=entry_id)
    )

    results = []
    for rel in rels:
        other_id = rel.entry2_id if rel.entry1_id == entry_id else rel.entry1_id
        other = Entry.objects.filter(
            id=other_id, deleted_at__isnull=True
        ).select_related('context').prefetch_related('tags').first()
        if not other:
            continue
        result = _format_relation(rel)
        result["other_entry"] = _format_entry(other, max_content_length=max_content_length)
        results.append(result)
    return results


def _query_annual_events(start_dt, end_dt, today_mmdd):
    """Query annual entries matching date range, respecting priority rules.

    p=1: show for any date in range. p=2+/None: show only for today.
    Returns queryset of Entry objects.
    """
    start_mmdd = start_dt.month * 100 + start_dt.day
    end_mmdd = end_dt.month * 100 + end_dt.day

    if (end_dt.date() - start_dt.date()).days >= 366:
        range_q = Q(priority=1)
    elif start_mmdd <= end_mmdd:
        range_q = Q(mmdd__gte=start_mmdd, mmdd__lte=end_mmdd, priority=1)
    else:
        range_q = (Q(mmdd__gte=start_mmdd, priority=1) | Q(mmdd__lte=end_mmdd, priority=1))

    q = range_q | Q(mmdd=today_mmdd)

    return Entry.objects.filter(
        q, kind='journal', deleted_at__isnull=True, mmdd__isnull=False
    ).select_related('context').prefetch_related('tags')


# --- Service functions ---

def get_calendar(start_date=None, end_date=None, context=None, days=None):
    start, err = _parse_date(start_date)
    if err:
        return {"error": err}
    if not start:
        start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if days is not None:
        if not isinstance(days, int) or days < 0:
            return {"error": f"days must be a non-negative integer, got {days}"}
        end = start + timedelta(days=days)
    elif end_date:
        end, err = _parse_date(end_date)
        if err:
            return {"error": err}
        if not end:
            return {"error": f"Invalid end_date: {end_date}"}
    else:
        end = start + timedelta(days=30)

    qs = Entry.objects.filter(
        kind='journal',
        deleted_at__isnull=True,
        mmdd__isnull=True,  # Exclude annual entries (handled separately)
    ).select_related('context').prefetch_related('tags')

    if context:
        qs = qs.filter(context__name=context)

    start_ts = start.timestamp()
    end_ts = (end + timedelta(days=1)).timestamp()
    tz = get_timezone()

    results = []
    for entry in qs:
        if not entry.data or not isinstance(entry.data, dict):
            continue
        event_date = entry.data.get('event_date')
        if event_date is None or not isinstance(event_date, (int, float)):
            continue
        if start_ts <= event_date < end_ts:
            event_dt = datetime.fromtimestamp(event_date, tz=tz)
            md_match = re.match(r'\[([^\]]+)\]\(([^)]+)\)', entry.content)
            if md_match:
                title = md_match.group(1)
                url = md_match.group(2)
            else:
                title = entry.content
                url = None
            result = {
                'date': event_dt.strftime('%Y-%m-%d'),
                'day': event_dt.strftime('%a'),
                'time': event_dt.strftime('%H:%M'),
                'title': title,
            }
            if url:
                result['url'] = url
            results.append(result)

    # Inject annual events
    now_dt = datetime.now(tz)
    today_mmdd = now_dt.month * 100 + now_dt.day
    annual_entries = _query_annual_events(start, end, today_mmdd)
    # Track IDs already in results to avoid duplicates
    seen_ids = {r.get('id') for r in results if 'id' in r}
    for entry in annual_entries:
        if entry.id in seen_ids:
            continue
        # Project annual event into current year's date
        annual_month = entry.mmdd // 100
        annual_day = entry.mmdd % 100
        try:
            projected_dt = now_dt.replace(month=annual_month, day=annual_day, hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            continue  # e.g. Feb 29 in non-leap year

        result = {
            'date': projected_dt.strftime('%Y-%m-%d'),
            'day': projected_dt.strftime('%a'),
            'time': '00:00',
            'title': entry.content,
            'annual': True,
        }
        results.append(result)

    results.sort(key=lambda x: (x['date'], x['time']))
    return results


def get_profile():
    qs = Entry.objects.filter(
        kind='profile',
        deleted_at__isnull=True,
    ).select_related('context').prefetch_related('tags').order_by('-timestamp_modified')
    return [_format_entry(entry) for entry in qs]


def get_ai_guidance(context=None, location_name=None, audience=None):
    from django.db.models.functions import Coalesce
    qs = Entry.objects.filter(
        kind='ai',
        deleted_at__isnull=True,
    ).select_related('context').prefetch_related('tags').order_by(
        Coalesce('priority', 9999),  # priority-1 first, nulls last
        'context__name',
        '-timestamp_modified',
    )

    results = []
    for entry in qs:
        entry_data = entry.data if isinstance(entry.data, dict) else {}
        entry_audiences = entry_data.get('audiences')
        if entry_audiences and audience not in entry_audiences:
            continue
        entry_context = entry.context.name if entry.context else None
        if context is None:
            if entry_context is None:
                results.append(_format_entry(entry))
        elif entry_context is None or entry_context == context:
            results.append(_format_entry(entry))

    # Per-machine details entries are kind='memory' (facts about a machine, not
    # AI behavioral rules), so they don't appear in the kind='ai' query above
    # and don't leak between machines. They're fetched by entry_id regardless
    # of kind when location_name is supplied.
    if location_name:
        details_id = f"{location_name}_details"
        details = Entry.objects.select_related('context').filter(
            data__entry_id=details_id,
            deleted_at__isnull=True,
        ).prefetch_related('tags').first()
        if details:
            results.append(_format_entry(details))
        else:
            results.append({
                "kind": "info",
                "location_name": location_name,
                "message": (
                    f"No machine details entry found for "
                    f"location_name='{location_name}'. To add one, create an "
                    f"entry with kind='memory', context=None, and "
                    f"data={{'entry_id': '{details_id}'}} describing this "
                    f"machine (deployed apps, working dirs, local quirks, "
                    f"collaboration axes relevant to this host). Surface "
                    f"this notice to the user so the missing entry gets "
                    f"authored."
                ),
            })

    return results


def list_contexts():
    contexts = Context.objects.annotate(
        entry_count=Count('entry', filter=models.Q(entry__deleted_at__isnull=True))
    ).order_by('name')
    return [
        {"name": c.name, "title": c.title, "description": c.description, "entry_count": c.entry_count}
        for c in contexts
    ]


def create_entry(content, kind="memory", context=None, name=None, tags=None,
                 event_date=None, event_time=None, priority=None, status=None,
                 create_context=False, source_tags=None, data=None):
    stripped_content = content.strip() if content else ''
    if not stripped_content:
        return {"error": "content is required and cannot be empty"}
    if kind not in VALID_KINDS:
        return {"error": f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}"}
    if kind == 'bookmark' and context == 'picks' and is_noop_pick_bookmark(stripped_content, data):
        return {"error": "Invalid picks bookmark: __noop__ is a sentinel, not content"}
    if status is not None and status not in VALID_STATUSES:
        return {"error": f"Invalid status '{status}'. Must be one of: {', '.join(VALID_STATUSES)}"}
    if priority is not None:
        if not isinstance(priority, int) or priority < 1:
            return {"error": f"priority must be a positive integer, got {priority}"}
    if event_date:
        err = _validate_event_date(event_date)
        if err:
            return {"error": err}
    if event_time:
        err = _validate_event_time(event_time)
        if err:
            return {"error": err}

    now = time.time()

    # Deduplication: reject if same content+kind created within 60 seconds
    recent_cutoff = now - 60
    duplicate = Entry.objects.filter(
        content=content.strip(),
        kind=kind,
        timestamp_created__gte=recent_cutoff,
        deleted_at__isnull=True,
    ).exists()
    if duplicate:
        return {"error": "Duplicate entry - same content was just created"}

    # Bookmark URL dedup: if a bookmark with this URL already exists in any
    # state (including archived), refuse the new one. The picks agent's
    # in-prompt dedup is unreliable; this enforces it server-side so the
    # archive isn't repeatedly polluted with the same URL across daily runs.
    if kind == 'bookmark':
        import re as _re
        from django.db.models import Q
        url = None
        m = _re.search(r'\(\s*(https?://[^\s\)]+)\s*\)', content)
        if m:
            url = m.group(1).strip()
        else:
            m = _re.search(r'(https?://\S+)', content)
            if m:
                url = m.group(1).strip()
        if url:
            existing_bm = Entry.objects.filter(
                kind='bookmark',
                deleted_at__isnull=True,
            ).filter(
                Q(content__contains=f'({url})') | Q(content=url) | Q(content__startswith=url + ' ')
            ).order_by('-timestamp_modified').first()
            if existing_bm:
                return {
                    "error": f"Bookmark with URL already exists",
                    "existing_id": str(existing_bm.id),
                    "url": url,
                }

    context_obj = None
    if context:
        try:
            context_obj = Context.objects.get(name=context)
        except Context.DoesNotExist:
            if create_context:
                context_obj = Context.objects.create(
                    name=context,
                    timestamp_created=now,
                    timestamp_modified=now,
                )
            else:
                return {"error": f"Context '{context}' does not exist. Use list_contexts() to see valid contexts, or set create_context=True."}

    if name:
        existing = Entry.objects.filter(
            name=name,
            context=context_obj,
            deleted_at__isnull=True,
        ).exists()
        if existing:
            ctx_desc = f"context '{context}'" if context else "no context"
            return {"error": f"Name '{name}' already exists in {ctx_desc}. Names must be unique within a context."}

    actual_content = content
    if event_date:
        if event_time:
            hour, minute = int(event_time[:2]), int(event_time[2:])
        else:
            actual_content, hour, minute = _extract_time_from_content(content)
            if hour is None:
                hour, minute = 12, 0

    entry_data = data.copy() if data else {}
    if event_date:
        tz = get_timezone()
        dt = datetime.strptime(event_date, '%Y%m%d').replace(hour=hour, minute=minute, tzinfo=tz)
        entry_data['event_date'] = dt.timestamp()

    # Store actual line count for dashboard display (content may be shown truncated)
    line_count = len([l for l in actual_content.split('\n') if l.strip()])
    if line_count > 1:
        entry_data['content_lines'] = line_count

    if not entry_data:
        entry_data = None

    # Compute mmdd for annual events
    entry_mmdd = None
    if tags and 'annual' in tags and entry_data and 'event_date' in entry_data:
        event_dt = datetime.fromtimestamp(entry_data['event_date'], tz=get_app_tz())
        entry_mmdd = event_dt.month * 100 + event_dt.day

    entry = Entry.objects.create(
        id=str(uuid.uuid7()),
        content=actual_content,
        kind=kind,
        context=context_obj,
        name=name,
        priority=priority,
        status=status,
        data=entry_data,
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
        mmdd=entry_mmdd,
    )

    if tags:
        tag_list = [t.strip().lstrip(':') for t in tags.split(',') if t.strip()]
        for tag_name in tag_list:
            Tag.objects.create(tag_name=tag_name, entry=entry)
    else:
        tag_list = []

    if source_tags:
        for tag_name in source_tags:
            if tag_name not in tag_list:
                Tag.objects.create(tag_name=tag_name, entry=entry)

    if kind == 'bookmark':
        tag_bookmark(entry)

    from .tag_stats import rebuild_tag_stats
    rebuild_tag_stats()

    return _format_entry(entry)


def copy_calendar_entry(entry_id, event_date, event_time=None):
    """Copy a calendar/journal entry to a new date.

    ALWAYS use this tool to copy calendar entries. Do NOT manually create a new
    entry — this tool copies content, data (links etc.), context, and tags exactly.

    Args:
        entry_id: UUID of the source journal entry to copy.
        event_date: Target date in YYYYMMDD format.
        event_time: Optional new time in HHMM format. If omitted, preserves
                    the original entry's time.
    """
    err = _validate_event_date(event_date)
    if err:
        return {"error": err}
    if event_time:
        err = _validate_event_time(event_time)
        if err:
            return {"error": err}

    source = Entry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if not source:
        return {"error": f"Entry '{entry_id}' not found"}
    if source.kind != 'journal':
        return {"error": f"Entry is kind='{source.kind}', not journal"}

    # Determine time: use provided time, or preserve original time
    if event_time:
        hour, minute = int(event_time[:2]), int(event_time[2:])
    elif source.data and source.data.get('event_date'):
        orig_dt = datetime.fromtimestamp(source.data['event_date'], tz=get_timezone())
        hour, minute = orig_dt.hour, orig_dt.minute
    else:
        hour, minute = 12, 0

    # Build new event_date timestamp
    tz = get_timezone()
    dt = datetime.strptime(event_date, '%Y%m%d').replace(hour=hour, minute=minute, tzinfo=tz)

    # Copy data, replacing event_date
    new_data = source.data.copy() if source.data else {}
    new_data['event_date'] = dt.timestamp()

    now = time.time()
    entry = Entry.objects.create(
        id=str(uuid.uuid7()),
        content=source.content,
        kind='journal',
        context=source.context,
        data=new_data,
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )

    # Copy tags (except fromai, add it fresh)
    source_tags = list(source.tags.values_list('tag_name', flat=True))
    for tag_name in source_tags:
        if tag_name != 'fromai':
            Tag.objects.create(tag_name=tag_name, entry=entry)
    Tag.objects.create(tag_name='fromai', entry=entry)

    from .tag_stats import rebuild_tag_stats
    rebuild_tag_stats()

    return _format_entry(entry)


def get_todos(context=None, status=None, include_done=False, max_content_length=DEFAULT_MAX_CONTENT_LENGTH):
    if status is not None and status not in VALID_STATUSES:
        return {"error": f"Invalid status '{status}'. Must be one of: {', '.join(VALID_STATUSES)}"}

    qs = Entry.objects.filter(
        kind='todo',
        deleted_at__isnull=True,
    ).select_related('context').prefetch_related('tags')

    if context:
        qs = qs.filter(context__name=context)
    if status:
        qs = qs.filter(status=status)
    elif not include_done:
        qs = qs.exclude(status='done')

    qs = qs.order_by('-timestamp_modified')
    return [_format_entry(entry, max_content_length=max_content_length) for entry in qs]


# --- todo bangs -------------------------------------------------------------
# A todo bang line opens with 3+ bangs, modulo a markdown list prefix: the
# lightweight in-flow todo marker, written wherever the thought occurred.
# Extraction is live — derived on every call, no stored state. This is the
# single extraction rule; the /tjai/todo-bangs/ page and get_todo_bangs()
# both read it, so page and agent can never see different sets.

TODO_BANG_RE = re.compile(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)?!{3,}')

# SQL twin of TODO_BANG_RE, served by the partial index entries_todo_bangs
# (migration 0019) — keep the two literally identical or the planner
# cannot prove the index applies and the query degrades to a full scan.
TODO_BANG_SQL_RE = r'(^|\n)[ \t]*([-*+][ \t]+|[0-9]+[.)][ \t]+)?!{3,}'


def todo_bang_entries(context=None, limit=None):
    """Entries carrying bang lines, newest-modified first.

    Per entry: every bang line in document order, raw text, untouched — no
    reordering by bang count or recency. Dialog, archived, and deleted
    entries are excluded: a bang line in a dialog turn is a conversation
    artifact, not one of the user's todos.
    """
    qs = Entry.objects.filter(
        deleted_at__isnull=True,
        content__regex=TODO_BANG_SQL_RE,
    ).exclude(status='archive').exclude(
        # AI-session contexts. 'claude-code' is the legacy dialog context name.
        context__name__in=('claude-code',) + DIALOG_CONTEXTS,
    )
    if context:
        qs = qs.filter(context__name=context)
    candidates = list(qs.select_related('context').order_by('-timestamp_modified'))
    # Dialog exclusion as a candidate-scoped membership check — an
    # exclude() anti-join against every dialog tag row costs ~60ms.
    dialog_ids = set(Tag.objects.filter(
        tag_name=DIALOG_TAG, entry_id__in=[e.id for e in candidates],
    ).values_list('entry_id', flat=True))
    tz = get_app_tz()
    out = []
    for e in candidates:
        if e.id in dialog_ids:
            continue
        bang_lines = [
            {'line': i, 'text': line.rstrip()}
            for i, line in enumerate(e.content.split('\n'), start=1)
            if TODO_BANG_RE.match(line)
        ]
        if not bang_lines:
            continue
        data = e.data if isinstance(e.data, dict) else {}
        entry_id = data.get('entry_id')
        first_line = e.content.split('\n', 1)[0].strip().lstrip('#').strip()
        out.append({
            'title': (e.name or entry_id or first_line)[:60],
            'entry_id': entry_id,
            'uuid': str(e.id),
            'kind': e.kind,
            'context': e.context.name if e.context else None,
            'modified': datetime.fromtimestamp(
                float(e.timestamp_modified), tz).strftime('%Y-%m-%d'),
            'bang_lines': bang_lines,
        })
        if limit and len(out) >= limit:
            break
    return out


def get_todo_bangs(context=None, limit=None):
    """Bang-marked todo lines, formatted compactly for LLM reading."""
    entries = todo_bang_entries(context=context, limit=limit)
    out = []
    for e in entries:
        out.append({
            'title': e['title'],
            'url': (f"/tjai/entry/?entry_id={e['entry_id']}" if e['entry_id']
                    else f"/tjai/entry/?uuid={e['uuid']}"),
            'context': e['context'],
            'modified': e['modified'],
            'lines': [b['text'] for b in e['bang_lines']],
        })
    return {
        'entries': out,
        'entry_count': len(out),
        'line_count': sum(len(e['lines']) for e in out),
    }


def get_goals(context=None, status=None, include_done=False, max_content_length=DEFAULT_MAX_CONTENT_LENGTH):
    """Get all goal entries with relation counts."""
    if status is not None and status not in VALID_STATUSES:
        return {"error": f"Invalid status '{status}'. Must be one of: {', '.join(VALID_STATUSES)}"}

    qs = Entry.objects.filter(
        kind='goal',
        deleted_at__isnull=True,
    ).select_related('context').prefetch_related('tags')

    if context:
        qs = qs.filter(context__name=context)
    if status:
        qs = qs.filter(status=status)
    elif not include_done:
        qs = qs.exclude(status='done')

    qs = qs.order_by('-timestamp_modified')

    goals = []
    for entry in qs:
        g = _format_entry(entry, max_content_length=max_content_length)
        # Count relations for this entry (cheap aggregate, not N+1)
        rel_count = Relation.objects.filter(
            Q(entry1_id=entry.id) | Q(entry2_id=entry.id)
        ).count()
        g['relation_count'] = rel_count
        goals.append(g)
    return goals


def get_memories(context=None, limit=50, offset=0, start_date=None, end_date=None, max_content_length=DEFAULT_MAX_CONTENT_LENGTH):
    err = _validate_result_limit(limit)
    if err:
        return err
    err = _validate_offset(offset)
    if err:
        return err

    qs = Entry.objects.filter(
        kind='memory',
        deleted_at__isnull=True,
    ).select_related('context').prefetch_related('tags')

    if context:
        qs = qs.filter(context__name=context)

    qs, err = _apply_date_filter(qs, start_date, end_date)
    if err:
        return err

    qs = qs.order_by('-timestamp_modified')[offset:offset + limit]
    return [_format_entry(entry, max_content_length=max_content_length) for entry in qs]


def get_dialog(host, start_date=None, end_date=None, limit=None, offset=0, max_content_length=DEFAULT_MAX_CONTENT_LENGTH):
    if not host:
        return {"error": "host is required (e.g. 'ec2dev', 'MacbookPro', or 'all')"}
    if not start_date:
        return {"error": "start_date is required"}
    err = _validate_result_limit(limit)
    if err:
        return err
    err = _validate_offset(offset)
    if err:
        return err

    dialog_ids = Tag.objects.filter(tag_name=DIALOG_TAG).values_list('entry_id', flat=True)
    qs = Entry.objects.filter(
        id__in=dialog_ids,
        deleted_at__isnull=True,
    )
    if host != 'all':
        qs = qs.filter(data__hostname=host)

    qs, err = _apply_date_filter(qs, start_date, end_date)
    if err:
        return err

    qs = qs.order_by('timestamp_created')
    if limit is not None:
        qs = qs[offset:offset + limit]
    elif offset:
        qs = qs[offset:]
    tz = get_app_tz()
    turns = []
    for e in qs:
        data = e.data if isinstance(e.data, dict) else {}
        role = data.get('role', 'unknown')
        speaker_type = 'human' if role == 'user' else 'ai' if role == 'assistant' else 'unknown'
        if speaker_type == 'human':
            speaker = 'Torre'
        elif speaker_type == 'ai':
            speaker = data.get('client') or 'AI'
        else:
            speaker = 'Unknown'
        content = e.content
        if max_content_length and len(content) > max_content_length:
            content = safe_truncate(content, max_content_length, suffix='…')
        turns.append({
            'timestamp': datetime.fromtimestamp(e.timestamp_created, tz=tz).isoformat(),
            'role': role,
            'speaker': speaker,
            'speaker_type': speaker_type,
            'client': data.get('client', ''),
            'model': data.get('model', ''),
            'model_provider': data.get('model_provider', ''),
            'reasoning_effort': data.get('reasoning_effort', ''),
            'hostname': data.get('hostname', ''),
            'content': content,
        })
    return turns


_LOG_LEVELS = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}


def _clean_log_message(message):
    """Strip a duplicated 'YYYY-MM-DD HH:MM:SS LEVEL ' prefix that older
    DbLogHandler rows stored in the message body. timestamp/level/source are
    returned as separate fields, so the prefix is noise. Mirrors the web Agent
    Log page (views.agent_log_data)."""
    return re.sub(
        r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} '
        r'(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+',
        '', message or '', count=1)


def get_logs(source=None, level=None, contains=None, ref=None,
             start_date=None, end_date=None, limit=100,
             max_content_length=DEFAULT_MAX_CONTENT_LENGTH):
    """Read application log (AppLog) rows with optional filters.

    AppLog is a separate table (db_table='applog'), not an Entry — the
    entry-query tools cannot reach it. Returns newest-first list of
    {timestamp (ET), level, source, message, extra_data}."""
    limit = min(int(limit or 100), 500)
    tz = get_app_tz()
    qs = AppLog.objects.order_by('-timestamp')

    if source:
        qs = qs.filter(source=source)
    if level:
        lvl = _LOG_LEVELS.get(str(level).upper())
        if lvl is None:
            return {"error": f"Invalid level '{level}'. Use DEBUG|INFO|WARNING|ERROR|CRITICAL."}
        qs = qs.filter(level__gte=lvl)
    if contains:
        qs = qs.filter(message__icontains=contains)
    if ref:
        # Mirror views.agent_log_data: referenced in extra_data or the message.
        qs = qs.filter(
            Q(extra_data__entry_id=ref) |
            Q(extra_data__action_id=ref) |
            Q(message__icontains=ref[:8]))
    if start_date is not None:
        start_ts, err = parse_date_filter(start_date, tz=tz)
        if err:
            return {"error": err}
        if start_ts:
            qs = qs.filter(timestamp__gte=datetime.fromtimestamp(start_ts, tz=tz))
    if end_date is not None:
        end_ts, err = parse_date_filter(end_date, end_of_day=True, tz=tz)
        if err:
            return {"error": err}
        if end_ts:
            qs = qs.filter(timestamp__lte=datetime.fromtimestamp(end_ts, tz=tz))

    rows = []
    for log in qs[:limit]:
        msg = _clean_log_message(log.message)
        if max_content_length and len(msg) > max_content_length:
            msg = safe_truncate(msg, max_content_length, suffix='…')
        rows.append({
            'timestamp': log.timestamp.astimezone(tz).isoformat(),
            'level': log.levelname or str(log.level),
            'source': log.source,
            'message': msg,
            'extra_data': log.extra_data or {},
        })
    return rows


def _encode_capcom_cursor(notice):
    payload = json.dumps(
        [notice.timestamp.isoformat(), notice.id], separators=(',', ':'),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip('=')


def _decode_capcom_cursor(cursor):
    if not isinstance(cursor, str) or not cursor:
        return None, None, "cursor must be a non-empty string"
    try:
        padded = cursor + '=' * (-len(cursor) % 4)
        timestamp_text, notice_id = json.loads(
            base64.urlsafe_b64decode(padded.encode()).decode()
        )
        cursor_dt = datetime.fromisoformat(timestamp_text)
        if timezone.is_naive(cursor_dt):
            raise ValueError("cursor timestamp has no timezone")
        return cursor_dt, int(notice_id), None
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError,
            base64.binascii.Error) as exc:
        return None, None, f"Invalid cursor: {exc}"


def get_capcom(source=None, severity=None, since=None, unread_only=False,
               limit=100, cursor=None):
    """Read the active Capcom notice feed without changing read state."""
    err = _validate_result_limit(limit)
    if err:
        return err
    if not isinstance(unread_only, bool):
        return {"error": "unread_only must be true or false"}

    source_filter = None
    if source is not None:
        if not isinstance(source, str):
            return {"error": "source must be a string"}
        source_filter = source.strip().rstrip('-') or None

    severity_filter = None
    if severity is not None:
        if not isinstance(severity, str):
            return {"error": "severity must be a string"}
        severity_filter = severity.strip().lower()
        if severity_filter not in CAPCOM_SEVERITIES:
            return {
                "error": (
                    f"Invalid severity '{severity}'. Use "
                    f"{'|'.join(CAPCOM_SEVERITIES)}."
                )
            }

    tz = get_app_tz()
    qs = Notice.objects.filter(archived=False)
    if source_filter:
        qs = qs.filter(
            Q(source=source_filter) |
            Q(source__startswith=f'{source_filter}-')
        )
    if severity_filter:
        qs = qs.filter(severity=severity_filter)
    if since is not None:
        since_ts, err = parse_date_filter(since, tz=tz)
        if err:
            return {"error": err}
        if since_ts:
            qs = qs.filter(
                timestamp__gte=datetime.fromtimestamp(since_ts, tz=tz)
            )
    if unread_only:
        qs = qs.filter(was_read=False)

    matched_sources = list(
        qs.order_by().values_list('source', flat=True).distinct()
    )

    if cursor is not None:
        cursor_dt, cursor_id, err = _decode_capcom_cursor(cursor)
        if err:
            return {"error": err}
        qs = qs.filter(
            Q(timestamp__lt=cursor_dt) |
            Q(timestamp=cursor_dt, id__lt=cursor_id)
        )

    rows = list(qs.order_by('-timestamp', '-id')[:limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_capcom_cursor(rows[-1]) if has_more and rows else None

    return {
        'notices': [{
            'id': notice.id,
            'timestamp': notice.timestamp.astimezone(tz).isoformat(),
            'first_seen': notice.first_seen.astimezone(tz).isoformat(),
            'source': notice.source,
            'severity': notice.severity,
            'title': notice.title,
            'url': notice.url,
            'detail': (notice.data or {}).get('detail', ''),
            'was_read': notice.was_read,
            'count': notice.count,
        } for notice in rows],
        'returned_count': len(rows),
        'has_more': has_more,
        'next_cursor': next_cursor,
        'total_unread_global': Notice.objects.filter(
            archived=False, was_read=False,
        ).count(),
        'matched_sources': sorted(matched_sources),
        'filters': {
            'source': source_filter,
            'severity': severity_filter,
            'since': since,
            'unread_only': unread_only,
        },
    }


def get_bookmarks(context=None, limit=50, offset=0, start_date=None, end_date=None, max_content_length=DEFAULT_MAX_CONTENT_LENGTH):
    err = _validate_result_limit(limit)
    if err:
        return err
    err = _validate_offset(offset)
    if err:
        return err

    qs = Entry.objects.filter(
        kind='bookmark',
        deleted_at__isnull=True,
    ).select_related('context').prefetch_related('tags')

    if context:
        qs = qs.filter(context__name=context)

    qs, err = _apply_date_filter(qs, start_date, end_date)
    if err:
        return err

    qs = qs.order_by('-timestamp_modified')[offset:offset + limit]
    return [_format_entry(entry, max_content_length=max_content_length) for entry in qs]


def search_entries(query=None, kind=None, context=None, limit=50, offset=0, start_date=None, end_date=None, max_content_length=DEFAULT_MAX_CONTENT_LENGTH, order_by='time'):
    if kind is not None and kind not in VALID_KINDS:
        return {"error": f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}"}
    err = _validate_result_limit(limit)
    if err:
        return err
    err = _validate_offset(offset)
    if err:
        return err
    if order_by not in ('time', 'rank', 'size'):
        return {"error": "order_by must be one of: time, rank, size"}

    query = (query or '').strip()
    if not query and order_by == 'rank':
        return {"error": "order_by='rank' requires a non-empty query"}

    qs = Entry.objects.filter(deleted_at__isnull=True).select_related('context').prefetch_related('tags')

    if query:
        if not re.search(r'\w', query):
            # Punctuation-only queries (e.g. the !!! take-note marker) compile
            # to an empty tsquery under websearch_to_tsquery and match nothing;
            # search them as literal substrings instead.
            from django.db.models import FloatField, Value
            qs = qs.filter(content__icontains=query).annotate(
                rank=Value(0.0, output_field=FloatField()),
            )
        else:
            from django.contrib.postgres.search import SearchQuery, SearchRank

            # Full-text search with relevance ranking (replaces icontains substring match)
            # Uses websearch_to_tsquery for Google-style syntax: quoted phrases, -exclusions.
            try:
                search_query = SearchQuery(query, search_type='websearch', config='english')
            except Exception:
                # Fallback for malformed queries
                search_query = SearchQuery(query, config='english')

            qs = qs.filter(
                search_vector=search_query,
            ).annotate(
                rank=SearchRank('search_vector', search_query, normalization=1, cover_density=True),
            )

    if kind:
        qs = qs.filter(kind=kind)
    if context:
        qs = qs.filter(context__name=context)

    qs, err = _apply_date_filter(qs, start_date, end_date)
    if err:
        return err

    if order_by == 'rank':
        qs = qs.order_by('-rank', '-timestamp_modified')
    elif order_by == 'size':
        from django.db.models.functions import Length
        qs = qs.annotate(content_len=Length('content')).order_by('-content_len', '-timestamp_modified')
    else:
        qs = qs.order_by('-timestamp_modified')
    qs = qs[offset:offset + limit]
    return [_format_entry(entry, max_content_length=max_content_length) for entry in qs]


def get_entry(entry_id):
    if not entry_id:
        return {"error": "entry_id is required"}
    entry = Entry.objects.select_related('context').filter(
        id=entry_id,
        deleted_at__isnull=True,
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found"}
    result = _format_entry(entry)
    relations = _get_relations_for_entry(entry_id)
    if relations:
        result["relations"] = relations
    return result


def get_named_entries(name=None, context=None, max_content_length=DEFAULT_MAX_CONTENT_LENGTH):
    """Get entries that have a @name. If name given, return that specific entry."""
    qs = Entry.objects.filter(
        deleted_at__isnull=True,
        name__isnull=False,
    ).exclude(name='').select_related('context').prefetch_related('tags')

    if context:
        qs = qs.filter(context__name=context)

    if name:
        entry = qs.filter(name__iexact=name).first()
        if not entry:
            ctx_msg = f" in context '{context}'" if context else ""
            return {"error": f"No entry named '{name}'{ctx_msg}"}
        return _format_entry(entry, max_content_length=max_content_length)

    qs = qs.order_by('name')
    return [_format_entry(entry, max_content_length=max_content_length) for entry in qs]


def get_entry_by_entry_id(entry_id):
    """Find an entry by its human-readable entry_id stored in data.entry_id."""
    if not entry_id:
        return {"error": "entry_id is required"}
    entry = Entry.objects.select_related('context').filter(
        data__entry_id=entry_id,
        deleted_at__isnull=True,
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"No entry found with entry_id '{entry_id}'"}
    return _format_entry(entry)


def edit_entry_metadata(entry_id, context=None, clear_context=False, tags=None,
                        event_date=None, event_time=None, clear_event_date=False,
                        priority=None, clear_priority=False,
                        status=None, clear_status=False,
                        name=None, clear_name=False, keep_time=False, data=None,
                        source='api'):
    """Edit an entry's metadata fields (tags, status, priority, context, name,
    event date/time, data, …) without touching its content. For content
    changes use replace_entry_content or append_entry_content."""
    return _edit_entry_impl(
        entry_id=entry_id, content=None,
        context=context, clear_context=clear_context, tags=tags,
        event_date=event_date, event_time=event_time, clear_event_date=clear_event_date,
        priority=priority, clear_priority=clear_priority,
        status=status, clear_status=clear_status,
        name=name, clear_name=clear_name, keep_time=keep_time, data=data,
        source=source,
    )


def replace_entry_content(entry_id, content, source='api'):
    """Replace an entry's content with the supplied text (destructive — the
    previous content is gone from the current version; it remains in version
    history and can be recovered via restore_version). Use when you genuinely
    want a full rewrite; use append_entry_content when you want to add to it."""
    if not entry_id:
        return {"error": "entry_id is required"}
    if content is None or not content.strip():
        return {"error": "content is required and cannot be empty"}
    return _edit_entry_impl(entry_id=entry_id, content=content, source=source)


def append_entry_content(entry_id, content, separator="\n\n", source='api'):
    """Append text to an entry's existing content. Final content is
    `existing + separator + content`. Existing content is always preserved.
    Use this for log-style entries, agent report-back, shopping lists, etc."""
    if not entry_id:
        return {"error": "entry_id is required"}
    if content is None or not content.strip():
        return {"error": "content is required and cannot be empty"}
    with transaction.atomic():
        entry = Entry.objects.select_for_update().filter(
            id=entry_id, deleted_at__isnull=True
        ).first()
        if not entry:
            return {"error": f"Entry '{entry_id}' not found or already deleted"}
        existing = entry.content or ""
        sep = separator if separator is not None else ""
        new_content = (existing + sep + content) if existing else content
        return _edit_entry_impl(
            entry_id=entry_id,
            content=new_content,
            source=source,
            locked_entry=entry,
        )


def _content_unified_diff(entry, before, after):
    """Return a complete unified diff for an entry content change."""
    data = entry.data if isinstance(entry.data, dict) else {}
    label = data.get('entry_id') or entry.name or str(entry.id)
    return '\n'.join(difflib.unified_diff(
        (before or '').splitlines(),
        (after or '').splitlines(),
        fromfile=f'{label}:before',
        tofile=f'{label}:after',
        lineterm='',
    ))


def _check_precondition(entry, expected_modified_at):
    """Verify the entry's current modified_at matches the client's expectation.
    Returns an error dict on mismatch, or None if precondition holds (or absent)."""
    if expected_modified_at is None:
        return None
    current_iso = datetime.fromtimestamp(entry.timestamp_modified, tz=get_app_tz()).isoformat()
    if current_iso != expected_modified_at:
        return {
            "error": "Entry was modified since expected_modified_at",
            "code": "STALE_PRECONDITION",
            "current_modified_at": current_iso,
            "expected_modified_at": expected_modified_at,
        }
    return None


_HEADING_RE = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$')


def _find_section(content, heading, level=None, occurrence=None):
    """Locate a markdown heading and return its body line range.

    Args:
        content: full entry content.
        heading: heading text to match (exact, after stripping the leading
                 '#' markers and whitespace).
        level: optional heading depth (1-6) to disambiguate.
        occurrence: 1-based index when multiple headings match. If None and
                    >1 match, returns ('multiple', count).

    Returns: tuple (status, *details).
        ('found', heading_line_idx, body_start, body_end, total_matches) — body lines are
            content.split('\\n')[body_start:body_end].
        ('not_found', 0)
        ('multiple', count) — only when occurrence is None and count > 1.
        ('out_of_range', count) — occurrence supplied but out of bounds.
    """
    lines = content.split('\n')
    matches = []
    in_fence = False
    target = heading.strip()
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith('```') or stripped.startswith('~~~'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if not m:
            continue
        line_level = len(m.group(1))
        if level is not None and line_level != level:
            continue
        if m.group(2).strip() != target:
            continue
        matches.append((i, line_level))

    count = len(matches)
    if count == 0:
        return ('not_found', 0)
    if count > 1 and occurrence is None:
        return ('multiple', count)
    chosen = (occurrence or 1) - 1
    if chosen < 0 or chosen >= count:
        return ('out_of_range', count)

    heading_line_idx, heading_level = matches[chosen]
    body_start = heading_line_idx + 1
    body_end = len(lines)
    in_fence = False
    for j in range(body_start, len(lines)):
        stripped = lines[j].lstrip()
        if stripped.startswith('```') or stripped.startswith('~~~'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(lines[j])
        if m and len(m.group(1)) <= heading_level:
            body_end = j
            break
    return ('found', heading_line_idx, body_start, body_end, count)


def replace_text_in_entry(entry_id, old_text, new_text, replace_all=False,
                          expected_modified_at=None, source='api'):
    """Surgical exact-match replace within an entry's content. Replaces
    `old_text` with `new_text`. Errors if `old_text` is absent, or if it
    occurs more than once and `replace_all` is False (supply more
    surrounding context to disambiguate, or set `replace_all=True`).

    Use this instead of `replace_entry_content` whenever you only want to
    change a small portion of a long entry — sending the whole body back is
    expensive in token output. Pattern matches Claude Code's `Edit` tool.

    Args:
        entry_id: UUID of the entry (required).
        old_text: exact substring to find (required, non-empty).
        new_text: replacement text (required; may be empty for deletion).
        replace_all: replace every occurrence. Default False.
        expected_modified_at: optional ISO modification timestamp from a
            prior read; if supplied and the entry has changed since, returns
            STALE_PRECONDITION instead of writing.

    Returns:
        On success: the updated entry dict (same shape as get_entry) plus
            'replaced_count'.
        On error: {"error": "...", "code": "..."} with code one of NOT_FOUND,
            BAD_REQUEST, NO_MATCH, MULTIPLE_MATCHES, STALE_PRECONDITION,
            EMPTY_RESULT.
    """
    if not entry_id:
        return {"error": "entry_id is required", "code": "BAD_REQUEST"}
    if old_text is None or old_text == "":
        return {"error": "old_text is required and cannot be empty", "code": "BAD_REQUEST"}
    if new_text is None:
        return {"error": "new_text is required (use empty string to delete)", "code": "BAD_REQUEST"}
    with transaction.atomic():
        entry = Entry.objects.select_for_update().filter(
            id=entry_id, deleted_at__isnull=True
        ).first()
        if not entry:
            return {"error": f"Entry '{entry_id}' not found or already deleted", "code": "NOT_FOUND"}
        err = _check_precondition(entry, expected_modified_at)
        if err:
            return err

        existing = entry.content or ""
        count = existing.count(old_text)
        if count == 0:
            return {"error": "old_text not found in entry content", "code": "NO_MATCH"}
        if count > 1 and not replace_all:
            return {
                "error": (f"old_text matched {count} times; pass replace_all=True to "
                          "replace all, or supply more surrounding context to make it unique"),
                "code": "MULTIPLE_MATCHES",
                "count": count,
            }

        if replace_all:
            new_content = existing.replace(old_text, new_text)
            replaced = count
        else:
            new_content = existing.replace(old_text, new_text, 1)
            replaced = 1

        if new_content == existing:
            return {"error": "old_text and new_text are identical; no change would result",
                    "code": "NOOP_PATCH"}
        if not new_content.strip():
            return {"error": "Result would be empty content; use delete_entry instead",
                    "code": "EMPTY_RESULT"}

        result = _edit_entry_impl(
            entry_id=entry_id,
            content=new_content,
            source=source,
            locked_entry=entry,
        )
        if isinstance(result, dict) and "error" not in result:
            result["replaced_count"] = replaced
        return result


def replace_section_in_entry(entry_id, heading, new_body, level=None, occurrence=None,
                             expected_modified_at=None, source='api'):
    """Replace the body under a markdown heading. Heading line itself is
    preserved; everything from the line after the heading up to the next
    heading at the same OR higher level is replaced with `new_body`.

    Use this for compressing or rewriting a structured section (e.g., a
    bullet list under a `##` heading) without sending the surrounding
    document back. Eliminates the 'rewrite a 4kB entry to change 200 bytes'
    tax that `replace_entry_content` imposes.

    Heading match is exact text after the `#` markers. Headings inside
    fenced code blocks (``` or ~~~) are ignored. If the heading text
    appears more than once, you must specify `level` or `occurrence`.

    Args:
        entry_id: UUID of the entry (required).
        heading: exact heading text without leading '#' or trailing whitespace.
        new_body: replacement body text (may be empty). Provide your own
            blank-line padding if you want it around the section — this
            tool does not add markdown formatting magic.
        level: optional heading depth (1-6) to disambiguate.
        occurrence: 1-based index if multiple headings match. None requires
            uniqueness (returns MULTIPLE_HEADINGS otherwise).
        expected_modified_at: optional ISO modification timestamp from a
            prior read.

    Returns:
        On success: the updated entry dict + 'section_lines_replaced' and
            'heading_line_index'.
        On error: {"error": "...", "code": "..."} with code one of NOT_FOUND,
            BAD_REQUEST, HEADING_NOT_FOUND, MULTIPLE_HEADINGS,
            OCCURRENCE_OUT_OF_RANGE, STALE_PRECONDITION, EMPTY_RESULT.
    """
    if not entry_id:
        return {"error": "entry_id is required", "code": "BAD_REQUEST"}
    if not heading:
        return {"error": "heading is required", "code": "BAD_REQUEST"}
    if new_body is None:
        return {"error": "new_body is required (use empty string for an empty section)",
                "code": "BAD_REQUEST"}
    if level is not None and (not isinstance(level, int) or not 1 <= level <= 6):
        return {"error": f"level must be an int 1-6, got {level!r}", "code": "BAD_REQUEST"}
    with transaction.atomic():
        entry = Entry.objects.select_for_update().filter(
            id=entry_id, deleted_at__isnull=True
        ).first()
        if not entry:
            return {"error": f"Entry '{entry_id}' not found or already deleted", "code": "NOT_FOUND"}
        err = _check_precondition(entry, expected_modified_at)
        if err:
            return err

        existing = entry.content or ""
        found = _find_section(existing, heading, level=level, occurrence=occurrence)
        status = found[0]
        if status == 'not_found':
            msg = f"No heading matching {heading!r}"
            if level is not None:
                msg += f" at level {level}"
            return {"error": msg, "code": "HEADING_NOT_FOUND"}
        if status == 'multiple':
            return {"error": (f"Found {found[1]} headings matching {heading!r}; "
                              "specify level or occurrence to disambiguate"),
                    "code": "MULTIPLE_HEADINGS", "count": found[1]}
        if status == 'out_of_range':
            return {"error": (f"occurrence out of range: only {found[1]} matching "
                              f"heading(s) exist"),
                    "code": "OCCURRENCE_OUT_OF_RANGE", "count": found[1]}

        _, h_idx, b_start, b_end, total_matches = found
        lines = existing.split('\n')
        new_body_lines = new_body.split('\n')
        new_lines = lines[:b_start] + new_body_lines + lines[b_end:]
        new_content = '\n'.join(new_lines)

        if new_content == existing:
            return {"error": "new_body matches the existing section body; no change would result",
                    "code": "NOOP_PATCH"}
        if not new_content.strip():
            return {"error": "Result would be empty content; use delete_entry instead",
                    "code": "EMPTY_RESULT"}

        result = _edit_entry_impl(
            entry_id=entry_id,
            content=new_content,
            source=source,
            locked_entry=entry,
        )
        if isinstance(result, dict) and "error" not in result:
            result["section_lines_replaced"] = b_end - b_start
            result["heading_line_index"] = h_idx
        return result


def edit_entry(entry_id, content=None, source='api', **kwargs):
    """Deprecated back-compat shim. Preserved undocumented for callers that
    still use the old combined API. New code: edit_entry_metadata for metadata,
    replace_entry_content / append_entry_content for content. This shim
    forwards to _edit_entry_impl with the same semantics as before the split."""
    return _edit_entry_impl(
        entry_id=entry_id, content=content, source=source, **kwargs
    )


def _edit_entry_impl(entry_id, content=None, context=None, clear_context=False,
                     tags=None, event_date=None, event_time=None, clear_event_date=False,
                     priority=None, clear_priority=False, status=None, clear_status=False,
                     name=None, clear_name=False, keep_time=False, data=None,
                     source='api', locked_entry=None):
    if not entry_id:
        return {"error": "entry_id is required"}
    if content is not None and len(content) < 10:
        return {"error": "content too short - must be at least 10 characters"}

    if event_date:
        err = _validate_event_date(event_date)
        if err:
            return {"error": err}
    if event_time:
        err = _validate_event_time(event_time)
        if err:
            return {"error": err}
    if priority is not None and (not isinstance(priority, int) or priority < 1):
        return {"error": f"priority must be a positive integer, got {priority}"}
    if status is not None and status not in VALID_STATUSES:
        return {"error": f"Invalid status '{status}'. Must be one of: {', '.join(VALID_STATUSES)}"}

    with transaction.atomic():
        entry = locked_entry
        if entry is None:
            entry = Entry.objects.select_for_update(of=('self',)).select_related('context').filter(
                id=entry_id,
                deleted_at__isnull=True,
            ).prefetch_related('tags').first()
        if not entry:
            return {"error": f"Entry '{entry_id}' not found or already deleted"}

        return _apply_entry_edit(
            entry=entry,
            content=content,
            context=context,
            clear_context=clear_context,
            tags=tags,
            event_date=event_date,
            event_time=event_time,
            clear_event_date=clear_event_date,
            priority=priority,
            clear_priority=clear_priority,
            status=status,
            clear_status=clear_status,
            name=name,
            clear_name=clear_name,
            keep_time=keep_time,
            data=data,
            source=source,
        )


def _apply_entry_edit(entry, content=None, context=None, clear_context=False,
                      tags=None, event_date=None, event_time=None,
                      clear_event_date=False, priority=None,
                      clear_priority=False, status=None, clear_status=False,
                      name=None, clear_name=False, keep_time=False, data=None,
                      source='api'):
    from .signals import entry_change_source

    if content is not None:
        actual_content = content
        hour, minute = None, None
        if event_date and not event_time:
            actual_content, hour, minute = _extract_time_from_content(content)
        old_content = entry.content
        entry.content = actual_content
    else:
        actual_content = entry.content
        old_content = entry.content
        hour, minute = None, None

    if context is not None:
        try:
            context_obj = Context.objects.get(name=context)
            entry.context = context_obj
        except Context.DoesNotExist:
            return {"error": f"Context '{context}' does not exist. Use list_contexts() to see valid contexts."}
    elif clear_context:
        entry.context = None

    tags_changed = False
    if tags is not None:
        entry.tags.all().delete()
        for tag_name in tags:
            if tag_name and tag_name.strip():
                Tag.objects.create(tag_name=tag_name.strip().lstrip(':'), entry=entry)
        tags_changed = True

    if event_date or clear_event_date:
        if entry.data is None:
            entry.data = {}
        if clear_event_date:
            entry.data.pop('event_date', None)
        else:
            tz = get_timezone()
            if event_time:
                h, m = int(event_time[:2]), int(event_time[2:])
            elif hour is not None:
                h, m = hour, minute
            else:
                h, m = 12, 0
            dt = datetime.strptime(event_date, '%Y%m%d').replace(hour=h, minute=m, tzinfo=tz)
            entry.data['event_date'] = dt.timestamp()
        if not entry.data:
            entry.data = None

    if data is not None:
        if entry.data is None:
            entry.data = {}
        for k, v in data.items():
            if v is None:
                entry.data.pop(k, None)
            else:
                entry.data[k] = v
        if not entry.data:
            entry.data = None

    if priority is not None:
        entry.priority = priority
    elif clear_priority:
        entry.priority = None

    if status is not None:
        entry.status = status
    elif clear_status:
        entry.status = None

    if name is not None:
        entry.name = name
    elif clear_name:
        entry.name = None

    # Update content_lines when content changes
    if actual_content != old_content:
        line_count = len([l for l in actual_content.split('\n') if l.strip()])
        if entry.data is None:
            entry.data = {}
        if line_count > 1:
            entry.data['content_lines'] = line_count
        else:
            entry.data.pop('content_lines', None)

    # Auto-preserve mod time if only tags and/or context changed
    metadata_only = (actual_content == old_content and
                     not event_date and not clear_event_date and
                     not data and
                     priority is None and not clear_priority and
                     status is None and not clear_status and
                     name is None and not clear_name)
    if not keep_time and not metadata_only:
        entry.timestamp_modified = time.time()

    entry.is_dirty = 1
    update_fields = ['content', 'context', 'data', 'priority', 'status', 'name', 'is_dirty']
    if not keep_time and not metadata_only:
        update_fields.append('timestamp_modified')
    with entry_change_source(source):
        entry.save(update_fields=update_fields)

    if tags_changed:
        from .tag_stats import rebuild_tag_stats
        rebuild_tag_stats()

    result = _format_entry(entry)
    if content is not None:
        result['diff'] = _content_unified_diff(entry, old_content, actual_content)
    return result


@transaction.atomic
def change_entry_kind(entry_id, kind, source='api'):
    if not entry_id:
        return {"error": "entry_id is required"}
    if not kind:
        return {"error": "kind is required"}
    if kind not in VALID_KINDS:
        return {"error": f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}"}

    entry = Entry.objects.select_for_update(of=('self',)).select_related('context').filter(
        id=entry_id,
        deleted_at__isnull=True,
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found or already deleted"}

    if entry.kind == kind:
        return {"error": f"Entry is already kind '{kind}'"}

    entry.kind = kind
    update_fields = ['kind', 'is_dirty', 'timestamp_modified']

    # Promoting to journal: parse any leading date/time prefix from the
    # existing content (e.g. "20260526/12:45 Pick up Mom" or "tomorrow
    # 12:45 ..."), so a memory→journal flip picks up an inline date the
    # same way the web editor would.
    prefix_warnings = []
    if kind == 'journal':
        try:
            from .journal_editor import parse_journal_editor_prefix
            entry_data = entry.data if isinstance(entry.data, dict) else {}
            current_event_ts = entry_data.get('event_date')
            new_content, event_ts, prefix_warnings = parse_journal_editor_prefix(
                entry.content or '',
                current_event_ts,
                get_timezone(),
            )
            if event_ts is not None:
                if not isinstance(entry.data, dict):
                    entry.data = {}
                entry.data['event_date'] = event_ts
                entry.content = new_content
                update_fields += ['data', 'content']
        except (ValueError, TypeError, OverflowError, OSError):
            pass

    entry.is_dirty = 1
    entry.timestamp_modified = time.time()
    from .signals import entry_change_source
    with entry_change_source(source):
        entry.save(update_fields=update_fields)

    result = _format_entry(entry)
    if prefix_warnings:
        result['warnings'] = prefix_warnings
    return result


@transaction.atomic
def delete_entry(entry_id, content, source='api'):
    if not entry_id:
        return {"error": "entry_id is required"}
    if not content:
        return {"error": "content is required - use get_entry first to fetch content"}

    entry = Entry.objects.select_for_update(of=('self',)).select_related('context').filter(
        id=entry_id,
        deleted_at__isnull=True,
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found or already deleted"}

    if _strip_punct(entry.content) != _strip_punct(content):
        return {"error": "Content does not match entry. Use get_entry to fetch current content."}

    from .models import snapshot_entry
    snapshot_entry(entry, changed_by=f'{source}_delete')

    now = time.time()
    entry.deleted_at = now
    entry.timestamp_modified = now
    entry.is_dirty = 1
    entry.save(update_fields=['deleted_at', 'timestamp_modified', 'is_dirty'])

    return {"deleted": True, "entry": _format_entry(entry)}


def run_action(entry_id):
    """Execute a specific action entry immediately. Delegates to action_runner."""
    from .action_runner import run_action as _run_action
    return _run_action(entry_id)


# --- Goal and Relation functions ---

def create_goal(content, context=None, name=None, tags=None,
                priority=None, status=None, create_context=False,
                source_tags=None, data=None):
    """Create a goal entry. Convenience wrapper around create_entry with kind='goal'."""
    return create_entry(
        content=content, kind='goal', context=context, name=name, tags=tags,
        priority=priority, status=status, create_context=create_context,
        source_tags=source_tags, data=data,
    )


def get_goal(entry_id, max_content_length=DEFAULT_MAX_CONTENT_LENGTH):
    """Get a goal entry with all its relations and tagged entries.

    Returns relations (from the relations table) and tagged_entries
    (entries with data.rel_goal matching this goal's entry_id).
    """
    if not entry_id:
        return {"error": "entry_id is required"}
    entry = Entry.objects.select_related('context').filter(
        id=entry_id, deleted_at__isnull=True,
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found"}
    if entry.kind != 'goal':
        return {"error": f"Entry is kind='{entry.kind}', not goal"}
    result = _format_entry(entry)
    result["relations"] = _get_relations_for_entry(entry_id, max_content_length=max_content_length)

    # Also return entries with data.rel_goal matching this goal's entry_id
    goal_entry_id = (entry.data or {}).get('entry_id')
    if goal_entry_id:
        tagged = Entry.objects.filter(
            data__rel_goal=goal_entry_id,
            deleted_at__isnull=True,
        ).select_related('context').prefetch_related('tags').order_by('timestamp_modified')
        result["tagged_entries"] = [_format_entry(e, max_content_length=max_content_length) for e in tagged]
    else:
        result["tagged_entries"] = []

    return result


def create_relation(entry1_id, entry2_id, relation_type, data=None):
    """Create a relation between two entries."""
    if not entry1_id or not entry2_id:
        return {"error": "both entry1_id and entry2_id are required"}
    if not relation_type:
        return {"error": "relation_type is required"}
    if entry1_id == entry2_id:
        return {"error": "Cannot create a relation between an entry and itself"}

    # Normalize ordering: smaller UUID first for unique constraint
    if entry1_id > entry2_id:
        entry1_id, entry2_id = entry2_id, entry1_id

    # Verify both entries exist and are not deleted
    e1 = Entry.objects.filter(id=entry1_id, deleted_at__isnull=True).first()
    if not e1:
        return {"error": f"Entry '{entry1_id}' not found"}
    e2 = Entry.objects.filter(id=entry2_id, deleted_at__isnull=True).first()
    if not e2:
        return {"error": f"Entry '{entry2_id}' not found"}

    # One relation per pair
    existing = Relation.objects.filter(entry1_id=entry1_id, entry2_id=entry2_id).first()
    if existing:
        return {"error": f"Relation already exists between these entries (id: {existing.id})"}

    now = time.time()
    rel = Relation.objects.create(
        id=str(uuid.uuid7()),
        entry1_id=entry1_id,
        entry2_id=entry2_id,
        relation_type=relation_type,
        data=data,
        timestamp_created=now,
        timestamp_modified=now,
    )
    return _format_relation(rel)


def edit_relation(relation_id, relation_type=None, data=None):
    """Edit a relation's type and/or data."""
    if not relation_id:
        return {"error": "relation_id is required"}
    if relation_type is None and data is None:
        return {"error": "Nothing to edit — provide relation_type and/or data"}

    rel = Relation.objects.filter(id=relation_id).first()
    if not rel:
        return {"error": f"Relation '{relation_id}' not found"}

    if relation_type is not None:
        rel.relation_type = relation_type

    if data is not None:
        if rel.data is None:
            rel.data = {}
        for k, v in data.items():
            if v is None:
                rel.data.pop(k, None)
            else:
                rel.data[k] = v
        if not rel.data:
            rel.data = None

    rel.timestamp_modified = time.time()
    rel.save()
    return _format_relation(rel)


def delete_relation(relation_id):
    """Delete a relation (hard delete — relations are structural edges, not content)."""
    if not relation_id:
        return {"error": "relation_id is required"}

    rel = Relation.objects.filter(id=relation_id).first()
    if not rel:
        return {"error": f"Relation '{relation_id}' not found"}

    result = _format_relation(rel)
    rel.delete()
    return {"deleted": True, "relation": result}


def get_relations(entry_id, max_content_length=DEFAULT_MAX_CONTENT_LENGTH):
    """Get all relations for an entry."""
    if not entry_id:
        return {"error": "entry_id is required"}
    entry = Entry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found"}
    return _get_relations_for_entry(entry_id, max_content_length=max_content_length)


def get_relation_graph(entry_id, depth=2, kinds=None, max_content_length=DEFAULT_MAX_CONTENT_LENGTH):
    """Traverse the relation graph from an entry, returning the connected subgraph.

    BFS traversal up to `depth` hops. Explores all edges regardless of entry kind,
    then optionally filters the returned results by kinds.
    """
    if not entry_id:
        return {"error": "entry_id is required"}
    if not isinstance(depth, int) or depth < 1 or depth > 10:
        return {"error": "depth must be an integer between 1 and 10"}
    if kinds is not None:
        for k in kinds:
            if k not in VALID_KINDS:
                return {"error": f"Invalid kind '{k}'. Must be one of: {', '.join(VALID_KINDS)}"}

    visited_entries = {}   # id -> formatted entry
    visited_relations = {}  # id -> formatted relation
    queue = [(entry_id, 0)]

    while queue:
        current_id, current_depth = queue.pop(0)
        if current_id in visited_entries:
            continue

        entry = Entry.objects.filter(
            id=current_id, deleted_at__isnull=True
        ).select_related('context').prefetch_related('tags').first()
        if not entry:
            continue

        visited_entries[current_id] = _format_entry(entry, max_content_length=max_content_length)

        if current_depth < depth:
            rels = Relation.objects.filter(
                Q(entry1_id=current_id) | Q(entry2_id=current_id)
            )
            for rel in rels:
                if rel.id not in visited_relations:
                    visited_relations[rel.id] = _format_relation(rel)
                other_id = rel.entry2_id if rel.entry1_id == current_id else rel.entry1_id
                if other_id not in visited_entries:
                    queue.append((other_id, current_depth + 1))

    # Apply kind filtering to results
    if kinds:
        filtered_entries = {
            eid: e for eid, e in visited_entries.items() if e['kind'] in kinds
        }
        filtered_relations = {
            rid: r for rid, r in visited_relations.items()
            if r['entry1_id'] in filtered_entries and r['entry2_id'] in filtered_entries
        }
        return {
            "entries": list(filtered_entries.values()),
            "relations": list(filtered_relations.values()),
        }

    return {
        "entries": list(visited_entries.values()),
        "relations": list(visited_relations.values()),
    }


def get_entry_versions(entry_id, version=None, age=None, max_content_length=0):
    """Get version history for an entry.

    Args:
        entry_id: UUID of the entry.
        version: Specific version number (positive) or relative (-1 = previous, -2 = two back).
        age: Minimum age string (e.g. '24h', '7d') — returns the most recent version
             at least that old. If no version is that old (the entry's whole
             retained history is younger), returns the oldest available version
             instead, with a 'note' field, so there is always a comparison baseline.
        max_content_length: Truncate content (0 = full).

    Returns single version (if version or age specified) or a dict containing
    all versions.
    """
    from .models import EntryVersion, Entry
    import time as _time

    try:
        entry = Entry.objects.get(id=entry_id, deleted_at__isnull=True)
    except Entry.DoesNotExist:
        return {"error": "Entry not found"}

    versions = EntryVersion.objects.filter(entry_id=entry.pk).order_by('-version_num')

    if version is not None:
        if version < 0:
            # Relative: -1 = previous, -2 = two back
            idx = abs(version) - 1
            vlist = list(versions.values('id', 'version_num', 'content', 'data', 'changed_by', 'timestamp'))
            if idx >= len(vlist):
                return {"error": f"Only {len(vlist)} versions exist, cannot go back {abs(version)}"}
            v = vlist[idx]
        else:
            v = versions.filter(version_num=version).values(
                'id', 'version_num', 'content', 'data', 'changed_by', 'timestamp'
            ).first()
            if not v:
                return {"error": f"Version {version} not found"}
        return _format_version(v, max_content_length)

    if age is not None:
        cutoff = _time.time() - _parse_duration(age)
        v = versions.filter(timestamp__lte=cutoff).values(
            'id', 'version_num', 'content', 'data', 'changed_by', 'timestamp'
        ).first()
        if not v:
            # No version is that old. Rather than returning nothing — which
            # made callers like the ideation agent conclude "no changes to
            # assess" for a doc whose entire retained history is younger than
            # `age` — fall back to the oldest available version so there is
            # still a comparison baseline. Flag it so the caller knows the
            # baseline is younger than requested.
            v = versions.order_by('version_num').values(
                'id', 'version_num', 'content', 'data', 'changed_by', 'timestamp'
            ).first()
            if not v:
                return {"error": "Entry has no version history"}
            result = _format_version(v, max_content_length)
            result["note"] = (
                f"No version at least {age} old; returned the oldest available "
                f"(v{v['version_num']}) as the comparison baseline."
            )
            return result
        return _format_version(v, max_content_length)

    # No filter — return all versions wrapped in a dict. A bare empty list
    # becomes an empty MCP content response, which clients reject.
    result = []
    for v in versions.values('id', 'version_num', 'content', 'data', 'changed_by', 'timestamp')[:50]:
        result.append(_format_version(v, max_content_length if max_content_length else 100))
    return {"versions": result, "count": len(result)}


def _format_version(v, max_content_length):
    from datetime import datetime, timezone
    content = v['content'] or ''
    if max_content_length and len(content) > max_content_length:
        content = content[:max_content_length] + '...'
    dt = datetime.fromtimestamp(v['timestamp'], tz=timezone.utc).astimezone()
    return {
        "version_num": v['version_num'],
        "content": content,
        "data": v['data'],
        "changed_by": v['changed_by'],
        "timestamp": dt.strftime('%Y-%m-%d %H:%M:%S %Z'),
    }


def _parse_duration(s):
    """Parse duration string like '24h', '7d', '2w' into seconds."""
    import re
    m = re.match(r'^(\d+)\s*([hdwm])$', s.strip().lower())
    if not m:
        return 86400  # default 24h
    val, unit = int(m.group(1)), m.group(2)
    multipliers = {'h': 3600, 'd': 86400, 'w': 604800, 'm': 2592000}
    return val * multipliers.get(unit, 86400)


@transaction.atomic
def restore_version(entry_id, version=None, source='api'):
    """Restore an entry's content from a previous version. Server-side copy.

    Args:
        entry_id: UUID of the entry.
        version: Version number (positive) or relative offset (-1 = previous,
                 -2 = two back). Default: -1 (previous version).

    Returns the updated entry.
    """
    from .models import EntryVersion, Entry
    from .signals import entry_change_source

    if version is None:
        version = -1

    try:
        entry = Entry.objects.select_for_update().get(
            id=entry_id, deleted_at__isnull=True
        )
    except Entry.DoesNotExist:
        return {"error": "Entry not found"}

    versions = EntryVersion.objects.filter(entry_id=entry.pk).order_by('-version_num')

    if version < 0:
        idx = abs(version) - 1
        vlist = list(versions.values('version_num', 'content', 'data'))
        if idx >= len(vlist):
            return {"error": f"Only {len(vlist)} versions exist, cannot go back {abs(version)}"}
        v = vlist[idx]
    else:
        v = versions.filter(version_num=version).values('version_num', 'content', 'data').first()
        if not v:
            return {"error": f"Version {version} not found"}

    old_content = entry.content
    entry.content = v['content']
    if v['data'] is not None:
        entry.data = v['data']
    entry.timestamp_modified = time.time()
    entry.is_dirty = 1
    with entry_change_source(source):
        entry.save()

    result = _format_entry(entry)
    result['diff'] = _content_unified_diff(entry, old_content, entry.content)
    return result
