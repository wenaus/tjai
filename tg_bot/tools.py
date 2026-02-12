"""tjai tools for Claude - synchronous Django ORM access."""

import re
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Django setup must happen before model imports
import django
import os
import sys

# Add parent dir to path for Django project
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tjai_project.settings')
django.setup()

from django.db import models
from django.db.models import Count
from django.utils import timezone

from tjai_app.models import Entry, Context, Tag, SysConfig
from tj.commands.journal import parse_time
from tj.date_utils import parse_date_filter

VALID_KINDS = ('memory', 'todo', 'journal', 'profile', 'ai', 'bookmark', 'list')
VALID_STATUSES = ('active', 'done', 'blocked', 'archive')


def _parse_date(date_str: str):
    """Parse date string to datetime."""
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


def _validate_event_date(date_str: str):
    """Validate event_date is YYYYMMDD format."""
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


def _validate_event_time(time_str: str):
    """Validate event_time is HHMM format."""
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


def _extract_time_from_content(content: str) -> tuple[str, int | None, int | None]:
    """Extract time from start of content if present."""
    parts = content.strip().split(None, 1)
    if not parts:
        return content, None, None
    first = parts[0]
    try:
        hour, minute = parse_time(first)
        remaining = parts[1] if len(parts) > 1 else ""
        return remaining.strip(), hour, minute
    except ValueError:
        return content, None, None


def _format_entry(entry) -> dict:
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


# Tool definitions for Claude API
TOOL_DEFINITIONS = [
    {
        "name": "get_calendar",
        "description": "Get calendar/journal entries within a date range. Returns events with date, time, title, and optional URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date (YYYYMMDD). Default: today."},
                "end_date": {"type": "string", "description": "End date (YYYYMMDD). Default: start + 30 days."},
                "context": {"type": "string", "description": "Filter to entries in this context."},
                "days": {"type": "integer", "description": "Alternative to end_date - number of days from start."},
            },
        },
    },
    {
        "name": "get_profile",
        "description": "Get personal facts and preferences about the user.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_ai_guidance",
        "description": "Get behavioral instructions for AI assistants. Returns general guidance plus context-specific if context provided.",
        "input_schema": {
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "If provided, includes guidance for this context."},
            },
        },
    },
    {
        "name": "list_contexts",
        "description": "List all available contexts (projects/topics) with entry counts.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_todos",
        "description": "Get todo/task entries. Returns all incomplete todos (excludes done). Do NOT filter by status unless user specifically asks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "Filter to todos in this context."},
                "include_done": {"type": "boolean", "description": "If true, include completed todos."},
            },
        },
    },
    {
        "name": "get_memories",
        "description": "Get memory entries - general notes and information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "Filter to memories in this context."},
                "start_date": {"type": "string", "description": "Start of date range. Supports YYYYMMDD, 'yesterday', '3d', 'monday', etc. Default: 7 days ago."},
                "end_date": {"type": "string", "description": "End of date range. Default: now."},
                "limit": {"type": "integer", "description": "Maximum results. Default: 50."},
            },
        },
    },
    {
        "name": "get_bookmarks",
        "description": "Get saved bookmarks (URLs).",
        "input_schema": {
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "Filter to bookmarks in this context."},
                "start_date": {"type": "string", "description": "Start of date range. Supports YYYYMMDD, 'yesterday', '3d', 'monday', etc. Default: 7 days ago."},
                "end_date": {"type": "string", "description": "End of date range. Default: now."},
                "limit": {"type": "integer", "description": "Maximum results. Default: 50."},
            },
        },
    },
    {
        "name": "search_entries",
        "description": "Full-text search across entries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (case-insensitive)."},
                "kind": {"type": "string", "description": "Filter by type: memory, todo, journal, profile, ai, bookmark."},
                "context": {"type": "string", "description": "Filter to this context."},
                "start_date": {"type": "string", "description": "Start of date range. Supports YYYYMMDD, 'yesterday', '3d', 'monday', etc. Default: 7 days ago."},
                "end_date": {"type": "string", "description": "End of date range. Default: now."},
                "limit": {"type": "integer", "description": "Maximum results. Default: 50."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_entry",
        "description": "Create a new entry (memory, todo, journal event, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The entry text."},
                "kind": {"type": "string", "description": "Entry type: memory, todo, journal, profile, ai, bookmark. Default: memory."},
                "context": {"type": "string", "description": "Context/project to associate with."},
                "tags": {"type": "string", "description": "Comma-separated tags."},
                "event_date": {"type": "string", "description": "For journal: date in YYYYMMDD format."},
                "event_time": {"type": "string", "description": "For journal: time in HHMM format (e.g., 0900, 1430)."},
                "priority": {"type": "integer", "description": "Priority level (1=highest)."},
                "status": {"type": "string", "description": "Status: active, done, blocked, archive."},
            },
            "required": ["content"],
        },
    },
    {
        "name": "get_entry",
        "description": "Get a single entry by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "The UUID of the entry."},
            },
            "required": ["entry_id"],
        },
    },
    {
        "name": "edit_entry",
        "description": "Edit an existing entry.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "The UUID of the entry to edit."},
                "content": {"type": "string", "description": "New content text."},
                "status": {"type": "string", "description": "New status: active, done, blocked, archive."},
                "priority": {"type": "integer", "description": "New priority (1=highest)."},
            },
            "required": ["entry_id", "content"],
        },
    },
    {
        "name": "delete_entry",
        "description": "Delete an entry (soft delete).",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "The UUID of the entry to delete."},
                "content": {"type": "string", "description": "Entry content (for confirmation)."},
            },
            "required": ["entry_id", "content"],
        },
    },
]


# Tool implementations
def _get_timezone():
    """Get configured timezone."""
    tz_config = SysConfig.objects.filter(key='timezone').first()
    tz_name = tz_config.value if tz_config else 'America/New_York'
    return ZoneInfo(tz_name)


def get_calendar(start_date=None, end_date=None, context=None, days=None):
    """Get calendar entries."""
    start, err = _parse_date(start_date)
    if err:
        return {"error": err}
    if not start:
        start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

    if days is not None:
        end = start + timedelta(days=days)
    elif end_date:
        end, err = _parse_date(end_date)
        if err:
            return {"error": err}
    else:
        end = start + timedelta(days=30)

    qs = Entry.objects.filter(
        kind='journal',
        deleted_at__isnull=True,
    ).select_related('context')

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

    results.sort(key=lambda x: (x['date'], x['time']))
    return results


def get_profile():
    """Get profile entries."""
    qs = Entry.objects.filter(
        kind='profile',
        deleted_at__isnull=True,
    ).select_related('context').prefetch_related('tags').order_by('-timestamp_modified')
    return [_format_entry(entry) for entry in qs]


def get_ai_guidance(context=None):
    """Get AI guidance entries."""
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
    """List all contexts."""
    contexts = Context.objects.annotate(
        entry_count=Count('entry', filter=models.Q(entry__deleted_at__isnull=True))
    ).order_by('name')
    return [
        {"name": c.name, "title": c.title, "description": c.description, "entry_count": c.entry_count}
        for c in contexts
    ]


def get_todos(context=None, status=None, include_done=False):
    """Get todo entries."""
    if status is not None and status not in VALID_STATUSES:
        return {"error": f"Invalid status '{status}'"}

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


def _apply_date_filter(qs, start_date, end_date, default_start_days=7):
    """Apply date range filter to queryset."""
    start_ts, err = parse_date_filter(start_date, default_days_ago=default_start_days)
    if err:
        return None, {"error": err}

    end_ts, err = parse_date_filter(end_date, default_days_ago=0, end_of_day=True)
    if err:
        return None, {"error": err}

    if start_ts:
        qs = qs.filter(timestamp_modified__gte=start_ts)
    if end_ts:
        qs = qs.filter(timestamp_modified__lte=end_ts)

    return qs, None


def get_memories(context=None, start_date=None, end_date=None, limit=50):
    """Get memory entries."""
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


def get_bookmarks(context=None, start_date=None, end_date=None, limit=50):
    """Get bookmark entries."""
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


def search_entries(query, kind=None, context=None, start_date=None, end_date=None, limit=50):
    """Search entries."""
    if not query:
        return {"error": "query is required"}
    if kind is not None and kind not in VALID_KINDS:
        return {"error": f"Invalid kind '{kind}'"}

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


def create_entry(content, kind="memory", context=None, tags=None, event_date=None, event_time=None, priority=None, status=None):
    """Create a new entry."""
    if not content or not content.strip():
        return {"error": "content is required"}
    if kind not in VALID_KINDS:
        return {"error": f"Invalid kind '{kind}'"}
    if status is not None and status not in VALID_STATUSES:
        return {"error": f"Invalid status '{status}'"}
    if priority is not None and (not isinstance(priority, int) or priority < 1):
        return {"error": f"priority must be a positive integer"}
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
            return {"error": f"Context '{context}' does not exist"}

    actual_content = content
    if event_date:
        if event_time:
            hour, minute = int(event_time[:2]), int(event_time[2:])
        else:
            actual_content, hour, minute = _extract_time_from_content(content)
            if hour is None:
                hour, minute = 12, 0

    data = {}
    if event_date:
        tz_config = SysConfig.objects.filter(key='timezone').first()
        tz_name = tz_config.value if tz_config else 'America/New_York'
        tz = ZoneInfo(tz_name)
        dt = datetime.strptime(event_date, '%Y%m%d').replace(hour=hour, minute=minute, tzinfo=tz)
        data['event_date'] = dt.timestamp()
    if not data:
        data = None

    entry = Entry.objects.create(
        id=str(uuid.uuid4()),
        content=actual_content,
        kind=kind,
        context=context_obj,
        priority=priority,
        status=status,
        data=data,
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )

    if tags:
        tag_list = [t.strip() for t in tags.split(',') if t.strip()]
        for tag_name in tag_list:
            Tag.objects.create(tag_name=tag_name, entry=entry)

    Tag.objects.create(tag_name='fromai', entry=entry)
    Tag.objects.create(tag_name='fromtg', entry=entry)

    if kind == 'bookmark':
        from tjai_app.tagger import tag_bookmark
        tag_bookmark(entry)

    return _format_entry(entry)


def get_entry(entry_id):
    """Get a single entry."""
    if not entry_id:
        return {"error": "entry_id is required"}
    entry = Entry.objects.select_related('context').filter(
        id=entry_id, deleted_at__isnull=True
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found"}
    return _format_entry(entry)


def edit_entry(entry_id, content, status=None, priority=None):
    """Edit an entry."""
    if not entry_id:
        return {"error": "entry_id is required"}
    if not content:
        return {"error": "content is required"}

    entry = Entry.objects.select_related('context').filter(
        id=entry_id, deleted_at__isnull=True
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found"}

    entry.content = content
    if status is not None:
        if status not in VALID_STATUSES:
            return {"error": f"Invalid status '{status}'"}
        entry.status = status
    if priority is not None:
        if not isinstance(priority, int) or priority < 1:
            return {"error": "priority must be a positive integer"}
        entry.priority = priority

    entry.timestamp_modified = time.time()
    entry.is_dirty = 1
    entry.save(update_fields=['content', 'status', 'priority', 'timestamp_modified', 'is_dirty'])
    return _format_entry(entry)


def delete_entry(entry_id, content):
    """Delete an entry."""
    if not entry_id:
        return {"error": "entry_id is required"}
    if not content:
        return {"error": "content is required"}

    entry = Entry.objects.select_related('context').filter(
        id=entry_id, deleted_at__isnull=True
    ).prefetch_related('tags').first()
    if not entry:
        return {"error": f"Entry '{entry_id}' not found"}
    if entry.content != content:
        return {"error": "Content does not match"}

    now = time.time()
    entry.deleted_at = now
    entry.timestamp_modified = now
    entry.is_dirty = 1
    entry.save(update_fields=['deleted_at', 'timestamp_modified', 'is_dirty'])
    return {"deleted": True, "entry": _format_entry(entry)}


# Tool dispatcher
TOOL_FUNCTIONS = {
    "get_calendar": get_calendar,
    "get_profile": get_profile,
    "get_ai_guidance": get_ai_guidance,
    "list_contexts": list_contexts,
    "get_todos": get_todos,
    "get_memories": get_memories,
    "get_bookmarks": get_bookmarks,
    "search_entries": search_entries,
    "create_entry": create_entry,
    "get_entry": get_entry,
    "edit_entry": edit_entry,
    "delete_entry": delete_entry,
}


def execute_tool(name: str, arguments: dict) -> dict:
    """Execute a tool by name with given arguments."""
    if name not in TOOL_FUNCTIONS:
        return {"error": f"Unknown tool: {name}"}
    try:
        return TOOL_FUNCTIONS[name](**arguments)
    except Exception as e:
        return {"error": str(e)}
