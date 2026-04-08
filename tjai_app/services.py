"""Shared business logic for tjai entry management.

All functions are synchronous (Django ORM). MCP wraps with sync_to_async.
Returns dicts/lists, not ORM objects.
"""

import re
import time
import uuid
from datetime import datetime, timedelta

from django.db import models
from django.db.models import Count
from django.utils import timezone

from django.db.models import Q

from .models import Entry, Context, Tag, SysConfig, Relation
from .tagger import tag_bookmark
from .tjai_utils import fmt_datetime, get_app_tz
from tj.commands.journal import parse_time
from tj.date_utils import parse_date_filter

VALID_KINDS = ('memory', 'todo', 'journal', 'profile', 'ai', 'bookmark', 'list', 'action', 'goal')
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


def get_ai_guidance(context=None):
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
        tag_list = [t.strip() for t in tags.split(',') if t.strip()]
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


def get_todos(context=None, status=None, include_done=False, max_content_length=200):
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


def get_goals(context=None, status=None, include_done=False, max_content_length=200):
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


def get_memories(context=None, limit=50, start_date=None, end_date=None, max_content_length=200):
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
    return [_format_entry(entry, max_content_length=max_content_length) for entry in qs]


def get_dialog(host, start_date=None, end_date=None, max_content_length=200):
    if not host:
        return {"error": "host is required (e.g. 'ec2dev', 'MacbookPro', or 'all')"}
    if not start_date:
        return {"error": "start_date is required"}

    dialog_ids = Tag.objects.filter(tag_name='ccdialog').values_list('entry_id', flat=True)
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
    tz = get_app_tz()
    turns = []
    for e in qs:
        data = e.data if isinstance(e.data, dict) else {}
        content = e.content
        if max_content_length and len(content) > max_content_length:
            content = content[:max_content_length] + '…'
        turns.append({
            'timestamp': datetime.fromtimestamp(e.timestamp_created, tz=tz).isoformat(),
            'role': data.get('role', 'unknown'),
            'hostname': data.get('hostname', ''),
            'content': content,
        })
    return turns


def get_bookmarks(context=None, limit=50, start_date=None, end_date=None, max_content_length=200):
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
    return [_format_entry(entry, max_content_length=max_content_length) for entry in qs]


def search_entries(query, kind=None, context=None, limit=50, start_date=None, end_date=None, max_content_length=200, order_by='time'):
    if not query:
        return {"error": "query is required"}
    if kind is not None and kind not in VALID_KINDS:
        return {"error": f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}"}
    if not isinstance(limit, int) or limit < 1:
        return {"error": f"limit must be a positive integer, got {limit}"}

    from django.contrib.postgres.search import SearchQuery, SearchRank

    # Full-text search with relevance ranking (replaces icontains substring match)
    # Uses websearch_to_tsquery for Google-style syntax: quoted phrases, -exclusions
    try:
        search_query = SearchQuery(query, search_type='websearch', config='english')
    except Exception:
        # Fallback for malformed queries
        search_query = SearchQuery(query, config='english')

    qs = Entry.objects.filter(
        search_vector=search_query,
        deleted_at__isnull=True,
    ).annotate(
        rank=SearchRank('search_vector', search_query, normalization=1, cover_density=True),
    ).select_related('context').prefetch_related('tags')

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
        qs = qs.annotate(content_len=Length('content')).order_by('-content_len')
    else:
        qs = qs.order_by('-timestamp_modified')
    qs = qs[:limit]
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


def get_named_entries(name=None, context=None, max_content_length=200):
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


def edit_entry(entry_id, content=None, context=None, clear_context=False,
               tags=None, event_date=None, event_time=None, clear_event_date=False,
               priority=None, clear_priority=False, status=None, clear_status=False,
               name=None, clear_name=False, keep_time=False, data=None):
    from .signals import set_changed_by
    set_changed_by('api')
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

    entry = Entry.objects.select_related('context').filter(
        id=entry_id,
        deleted_at__isnull=True,
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found or already deleted"}

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
                Tag.objects.create(tag_name=tag_name.strip(), entry=entry)
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
    entry.save(update_fields=update_fields)

    if tags_changed:
        from .tag_stats import rebuild_tag_stats
        rebuild_tag_stats()

    return _format_entry(entry)


def change_entry_kind(entry_id, kind):
    if not entry_id:
        return {"error": "entry_id is required"}
    if not kind:
        return {"error": "kind is required"}
    if kind not in VALID_KINDS:
        return {"error": f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}"}

    entry = Entry.objects.select_related('context').filter(
        id=entry_id,
        deleted_at__isnull=True,
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found or already deleted"}

    if entry.kind == kind:
        return {"error": f"Entry is already kind '{kind}'"}

    entry.kind = kind
    entry.is_dirty = 1
    entry.save(update_fields=['kind', 'is_dirty'])

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

    from .models import snapshot_entry
    snapshot_entry(entry, changed_by='api_delete')

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


def get_goal(entry_id, max_content_length=200):
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


def get_relations(entry_id, max_content_length=200):
    """Get all relations for an entry."""
    if not entry_id:
        return {"error": "entry_id is required"}
    entry = Entry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found"}
    return _get_relations_for_entry(entry_id, max_content_length=max_content_length)


def get_web(entry_id, depth=2, kinds=None, max_content_length=200):
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
             at least that old.
        max_content_length: Truncate content (0 = full).

    Returns single version (if version or age specified) or list of all versions.
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
            return {"error": f"No version at least {age} old"}
        return _format_version(v, max_content_length)

    # No filter — return list of all versions (metadata only, content truncated)
    result = []
    for v in versions.values('id', 'version_num', 'content', 'data', 'changed_by', 'timestamp')[:50]:
        result.append(_format_version(v, max_content_length if max_content_length else 100))
    return result


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


def restore_version(entry_id, version=None):
    """Restore an entry's content from a previous version. Server-side copy.

    Args:
        entry_id: UUID of the entry.
        version: Version number (positive) or relative offset (-1 = previous,
                 -2 = two back). Default: -1 (previous version).

    Returns the updated entry.
    """
    from .models import EntryVersion, Entry
    from .signals import set_changed_by

    if version is None:
        version = -1

    try:
        entry = Entry.objects.get(id=entry_id, deleted_at__isnull=True)
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

    set_changed_by('api')
    entry.content = v['content']
    if v['data'] is not None:
        entry.data = v['data']
    entry.save()

    return _entry_to_dict(entry)
