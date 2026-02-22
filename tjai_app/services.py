"""Shared business logic for tjai entry management.

All functions are synchronous (Django ORM). MCP wraps with sync_to_async.
Returns dicts/lists, not ORM objects.
"""

import re
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import models
from django.db.models import Count
from django.utils import timezone

from django.db.models import Q

from .models import Entry, Context, Tag, SysConfig
from .tagger import tag_bookmark
from tj.commands.journal import parse_time
from tj.date_utils import parse_date_filter

VALID_KINDS = ('memory', 'todo', 'journal', 'profile', 'ai', 'bookmark', 'list', 'action')
VALID_STATUSES = ('active', 'done', 'blocked', 'archive')


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


def _get_timezone():
    """Get configured timezone with fallback to America/New_York."""
    tz_config = SysConfig.objects.filter(key='timezone').first()
    tz_name = tz_config.value if tz_config else 'America/New_York'
    return ZoneInfo(tz_name)


def _format_entry(entry):
    """Format an Entry object for API response."""
    result = {
        "id": entry.id,
        "content": entry.content,
        "kind": entry.kind,
        "context": entry.context.name if entry.context else None,
        "created": datetime.fromtimestamp(entry.timestamp_created).isoformat(),
        "modified": datetime.fromtimestamp(entry.timestamp_modified).isoformat(),
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
    tz = _get_timezone()
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


def _query_annual_events(start_dt, end_dt, today_mmdd):
    """Query annual entries matching date range, respecting priority rules.

    p=1: show for any date in range. p=2+/None: show only for today.
    Returns queryset of Entry objects.
    """
    start_mmdd = start_dt.month * 100 + start_dt.day
    end_mmdd = end_dt.month * 100 + end_dt.day

    if start_mmdd <= end_mmdd:
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
    tz = _get_timezone()

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


def get_ai_guidance(context=None):
    qs = Entry.objects.filter(
        kind='ai',
        deleted_at__isnull=True,
    ).select_related('context').prefetch_related('tags').order_by('context__name', '-timestamp_modified')

    results = []
    for entry in qs:
        entry_context = entry.context.name if entry.context else None
        if context:
            if entry_context is None or entry_context == context:
                results.append(_format_entry(entry))
        else:
            results.append(_format_entry(entry))
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
    if not content or not content.strip():
        return {"error": "content is required and cannot be empty"}
    if kind not in VALID_KINDS:
        return {"error": f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}"}
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
        tz = _get_timezone()
        dt = datetime.strptime(event_date, '%Y%m%d').replace(hour=hour, minute=minute, tzinfo=tz)
        entry_data['event_date'] = dt.timestamp()
    if not entry_data:
        entry_data = None

    # Compute mmdd for annual events
    entry_mmdd = None
    if tags and 'annual' in tags and entry_data and 'event_date' in entry_data:
        event_dt = datetime.fromtimestamp(entry_data['event_date'])
        entry_mmdd = event_dt.month * 100 + event_dt.day

    entry = Entry.objects.create(
        id=str(uuid.uuid4()),
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
        tag_list = [t.strip() for t in tags.split(',') if t.strip()]
        for tag_name in tag_list:
            Tag.objects.create(tag_name=tag_name, entry=entry)

    if source_tags:
        for tag_name in source_tags:
            Tag.objects.create(tag_name=tag_name, entry=entry)

    if kind == 'bookmark':
        tag_bookmark(entry)

    from .tag_stats import rebuild_tag_stats
    rebuild_tag_stats()

    return _format_entry(entry)


def get_todos(context=None, status=None, include_done=False):
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

    qs = qs.order_by(models.F('priority').asc(nulls_last=True), '-timestamp_modified')
    return [_format_entry(entry) for entry in qs]


def get_memories(context=None, limit=50, start_date=None, end_date=None):
    if not isinstance(limit, int) or limit < 1:
        return {"error": f"limit must be a positive integer, got {limit}"}

    qs = Entry.objects.filter(
        kind='memory',
        deleted_at__isnull=True,
    ).select_related('context').prefetch_related('tags')

    if context:
        qs = qs.filter(context__name=context)

    qs, err = _apply_date_filter(qs, start_date, end_date)
    if err:
        return err

    qs = qs.order_by('-timestamp_modified')[:limit]
    return [_format_entry(entry) for entry in qs]


def get_bookmarks(context=None, limit=50, start_date=None, end_date=None):
    if not isinstance(limit, int) or limit < 1:
        return {"error": f"limit must be a positive integer, got {limit}"}

    qs = Entry.objects.filter(
        kind='bookmark',
        deleted_at__isnull=True,
    ).select_related('context').prefetch_related('tags')

    if context:
        qs = qs.filter(context__name=context)

    qs, err = _apply_date_filter(qs, start_date, end_date)
    if err:
        return err

    qs = qs.order_by('-timestamp_modified')[:limit]
    return [_format_entry(entry) for entry in qs]


def search_entries(query, kind=None, context=None, limit=50, start_date=None, end_date=None):
    if not query:
        return {"error": "query is required"}
    if kind is not None and kind not in VALID_KINDS:
        return {"error": f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}"}
    if not isinstance(limit, int) or limit < 1:
        return {"error": f"limit must be a positive integer, got {limit}"}

    qs = Entry.objects.filter(
        content__icontains=query,
        deleted_at__isnull=True,
    ).select_related('context').prefetch_related('tags')

    if kind:
        qs = qs.filter(kind=kind)
    if context:
        qs = qs.filter(context__name=context)

    qs, err = _apply_date_filter(qs, start_date, end_date)
    if err:
        return err

    qs = qs.order_by('-timestamp_modified')[:limit]
    return [_format_entry(entry) for entry in qs]


def get_entry(entry_id):
    if not entry_id:
        return {"error": "entry_id is required"}
    entry = Entry.objects.select_related('context').filter(
        id=entry_id,
        deleted_at__isnull=True,
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found"}
    return _format_entry(entry)


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


def edit_entry(entry_id, content, context=None, clear_context=False,
               tags=None, event_date=None, event_time=None, clear_event_date=False,
               priority=None, clear_priority=False, status=None, clear_status=False,
               name=None, clear_name=False, keep_time=False, data=None):
    if not entry_id:
        return {"error": "entry_id is required"}
    if not content:
        return {"error": "content is required - must provide the new content"}
    if len(content) < 10:
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

    entry = Entry.objects.select_related('context').filter(
        id=entry_id,
        deleted_at__isnull=True,
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found or already deleted"}

    actual_content = content
    hour, minute = None, None
    if event_date and not event_time:
        actual_content, hour, minute = _extract_time_from_content(content)
    entry.content = actual_content

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
                Tag.objects.create(tag_name=tag_name.strip(), entry=entry)
        tags_changed = True

    if event_date or clear_event_date:
        if entry.data is None:
            entry.data = {}
        if clear_event_date:
            entry.data.pop('event_date', None)
        else:
            tz = _get_timezone()
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

    if not keep_time:
        entry.timestamp_modified = time.time()

    entry.is_dirty = 1
    update_fields = ['content', 'context', 'data', 'priority', 'status', 'name', 'is_dirty']
    if not keep_time:
        update_fields.append('timestamp_modified')
    entry.save(update_fields=update_fields)

    if tags_changed:
        from .tag_stats import rebuild_tag_stats
        rebuild_tag_stats()

    return _format_entry(entry)


def delete_entry(entry_id, content):
    if not entry_id:
        return {"error": "entry_id is required"}
    if not content:
        return {"error": "content is required - use get_entry first to fetch content"}

    entry = Entry.objects.select_related('context').filter(
        id=entry_id,
        deleted_at__isnull=True,
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found or already deleted"}

    if _strip_punct(entry.content) != _strip_punct(content):
        return {"error": "Content does not match entry. Use get_entry to fetch current content."}

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
