"""
MCP (Model Context Protocol) tools for tjai.

tjai is a personal AI memory and task management system. These tools provide
AI assistants with structured access to the user's knowledge base.

Available tools:
    get_calendar      - Retrieve journal entries for a date range (events, appointments)
    get_profile       - Get personal facts and preferences about the user
    get_ai_guidance   - Get behavioral instructions for AI assistants
    list_contexts     - List all projects/topics for organizing entries
    create_entry      - Add new entries (memories, todos, journal, profile, ai, bookmark)
    get_todos         - Retrieve todo items with filtering options
    get_memories      - Get memory entries (general notes)
    search_entries    - Full-text search across all entries
    get_entry         - Get a single entry by ID
    edit_entry        - Edit an existing entry
    delete_entry      - Soft delete an entry (requires user approval)

Entry types: memory, todo, journal, profile, bookmark, ai, list

Contexts group entries by project or topic. Most tools accept a context parameter
to filter results. Use get_ai_guidance(context) before starting work on any
project to get project-specific instructions.

Error handling: Tools return {"error": "message"} on validation failures.
Check for "error" key in response before processing results.
"""

import re
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from asgiref.sync import sync_to_async
from django.db import models
from django.db.models import Count
from django.utils import timezone
from mcp_server import mcp_server as mcp

from .models import Entry, Context, Tag, SysConfig
from tj.commands.journal import parse_time

VALID_KINDS = ('memory', 'todo', 'journal', 'profile', 'ai', 'bookmark', 'list')
VALID_STATUSES = ('active', 'done', 'blocked', 'archive')


def _parse_date(date_str: str):
    """Parse date string to datetime. Supports ISO format and YYYYMMDD.

    Returns:
        (datetime, None) on success
        (None, None) if date_str is empty/None
        (None, error_message) on parse failure
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


def _validate_event_date(date_str: str):
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


def _validate_event_time(time_str: str):
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


def _extract_time_from_content(content: str) -> tuple[str, int, int] | tuple[str, None, None]:
    """Extract time from start of content if present.

    Uses parse_time from CLI for consistent parsing.

    Returns:
        (remaining_content, hour, minute) if time found
        (original_content, None, None) if no time found
    """
    parts = content.strip().split(None, 1)  # Split on first whitespace
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


@mcp.tool()
async def get_calendar(
    start_date: str = None,
    end_date: str = None,
    context: str = None,
    days: int = None,
) -> list:
    """
    Get calendar/journal entries within a date range.

    Args:
        start_date: Start date (ISO format or YYYYMMDD). Default: today.
        end_date: End date (ISO format or YYYYMMDD). Default: start + 30 days.
        context: Filter to entries in this context/project.
        days: Alternative to end_date - number of days from start_date.

    Returns:
        List of entries sorted by date/time, each containing:
        - date: YYYY-MM-DD
        - day: Day of week (Mon, Tue, etc.)
        - time: HH:MM
        - title: Event title (plain text)
        - url: Link URL (only if event has a link)

    OUTPUT FORMAT: When presenting to user, show each entry as:
        TIME TITLE URL
    Example:
        **2026-01-15 Thu**
        - 14:00 HSF coord https://indico.cern.ch/e/1606598
        - 15:00 Team meeting
    Always include the URL after the title when present.
    """
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

    @sync_to_async
    def fetch():
        qs = Entry.objects.filter(
            kind='journal',
            deleted_at__isnull=True,
        ).select_related('context').prefetch_related('tags')

        if context:
            qs = qs.filter(context__name=context)

        # Convert date range to timestamps for comparison
        start_ts = start.timestamp()
        end_ts = (end + timedelta(days=1)).timestamp()  # Include full end day

        tz_config = SysConfig.objects.filter(key='timezone').first()
        if not tz_config:
            return [{"error": "SysConfig 'timezone' not set"}]
        tz = ZoneInfo(tz_config.value)

        results = []
        for entry in qs:
            if not entry.data or not isinstance(entry.data, dict):
                continue
            event_date = entry.data.get('event_date')
            if event_date is None:
                continue
            # event_date is stored as Unix timestamp (float)
            if not isinstance(event_date, (int, float)):
                continue
            if start_ts <= event_date < end_ts:
                event_dt = datetime.fromtimestamp(event_date, tz=tz)
                # Extract title and url from markdown link if present
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

    return await fetch()


@mcp.tool()
async def get_profile() -> list:
    """
    Get all profile entries about the user.

    Profile entries contain personal facts, preferences, and information
    that AI assistants should know about the user. Examples: preferred tools,
    working style, technical background, personal preferences.

    Call this early in a session to understand context about who you're helping.

    Returns:
        List of profile entries ordered by most recently modified, each containing:
        id, content, context, kind, created, modified, tags.
    """
    @sync_to_async
    def fetch():
        qs = Entry.objects.filter(
            kind='profile',
            deleted_at__isnull=True,
        ).select_related('context').prefetch_related('tags').order_by('-timestamp_modified')

        return [_format_entry(entry) for entry in qs]

    return await fetch()


@mcp.tool()
async def get_ai_guidance(context: str = None) -> list:
    """
    Get AI guidance entries - behavioral instructions for AI assistants.

    AI guidance entries define how AI assistants should behave. They include:
    - General guidance (no context): Universal rules applying to all interactions
    - Context-specific guidance: Rules for working on particular projects/topics

    IMPORTANT: Always call this before starting work on any context/project to
    get project-specific instructions. The user expects you to follow these.

    Args:
        context: If provided, returns general guidance PLUS guidance specific
                 to this context. If None, returns all guidance entries.

    Returns:
        List of AI guidance entries ordered by context then modification date,
        each containing: id, content, context (null for general), kind,
        created, modified, tags.
    """
    @sync_to_async
    def fetch():
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

    return await fetch()


@mcp.tool()
async def list_contexts() -> list:
    """
    List all available contexts (projects/topics).

    Contexts are organizational units that group entries by project or topic.
    Use this to discover what projects exist and to understand the scope of
    the user's knowledge base. Most other tools accept a context parameter
    to filter results.

    Returns:
        List of contexts ordered alphabetically by name, each containing:
        name (identifier), title (display name, may be null),
        description (may be null), entry_count (number of non-deleted entries).
    """
    @sync_to_async
    def fetch():
        contexts = Context.objects.annotate(
            entry_count=Count('entry', filter=models.Q(entry__deleted_at__isnull=True))
        ).order_by('name')

        return [
            {
                "name": c.name,
                "title": c.title,
                "description": c.description,
                "entry_count": c.entry_count,
            }
            for c in contexts
        ]

    return await fetch()


@mcp.tool()
async def create_entry(
    content: str,
    kind: str = "memory",
    context: str = None,
    name: str = None,
    tags: str = None,
    event_date: str = None,
    event_time: str = None,
    priority: int = None,
    status: str = None,
    create_context: bool = False,
) -> dict:
    """
    Create a new entry in the user's tjai knowledge base.

    Use this to record information, create tasks, add calendar events, or store
    any other data the user wants to remember. Entries sync across all the
    user's devices.

    Args:
        content: The entry text (required). Do NOT include time in content - use event_time.
        kind: Entry type. One of: memory (general notes, default), todo (tasks),
              journal (calendar events with event_date), profile (facts about user),
              ai (instructions for AI assistants), bookmark (URLs), list (lists).
        context: Context/project name to associate with. Must exist unless
                 create_context=True. Use list_contexts() to see existing contexts.
        name: Optional unique identifier for easy reference (e.g., @budget).
              Must be unique within the context. Fails if name already exists.
        tags: Comma-separated tags (e.g., "important,followup,dev").
        event_date: For journal entries - date in YYYYMMDD format (e.g., "20260128").
        event_time: For journal entries - time in HHMM 24-hour format (e.g., "0900" for 9am,
                    "1430" for 2:30pm). Defaults to 1200 (noon) if omitted.
        priority: Priority level where 1 is highest (positive integers only).
        status: Status value. One of: active, done, blocked, archive.
        create_context: If True, creates context if it doesn't exist. Default: False
                        (fails if context doesn't exist, preventing typos).

    Example for calendar event "Meeting at 9am on Jan 28, 2026":
        create_entry(content="Meeting", kind="journal", event_date="20260128", event_time="0900")

    Returns:
        The created entry with id, content, kind, context, created, modified,
        and any optional fields (name, priority, status, tags, data).
        Returns {"error": "..."} if validation fails.
    """
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

    @sync_to_async
    def create():
        now = time.time()

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

        # Extract time from content if not provided via event_time
        actual_content = content
        if event_date:
            if event_time:
                hour, minute = int(event_time[:2]), int(event_time[2:])
            else:
                # Try to extract time from content (e.g., "09:00 Pack boxes" -> "Pack boxes")
                actual_content, hour, minute = _extract_time_from_content(content)
                if hour is None:
                    hour, minute = 12, 0  # Default to noon

        data = {}
        if event_date:
            tz_config = SysConfig.objects.filter(key='timezone').first()
            if not tz_config:
                return {"error": "SysConfig 'timezone' not set"}
            tz_name = tz_config.value
            tz = ZoneInfo(tz_name)
            # Convert to timestamp in user's timezone
            dt = datetime.strptime(event_date, '%Y%m%d').replace(hour=hour, minute=minute, tzinfo=tz)
            data['event_date'] = dt.timestamp()
        if not data:
            data = None

        entry = Entry.objects.create(
            id=str(uuid.uuid4()),
            content=actual_content,
            kind=kind,
            context=context_obj,
            name=name,
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

        # Always tag MCP-created entries
        Tag.objects.create(tag_name='fromai', entry=entry)

        return _format_entry(entry)

    return await create()


@mcp.tool()
async def get_todos(
    context: str = None,
    status: str = None,
    include_done: bool = False,
) -> list:
    """
    Get todo/task entries.

    Retrieves the user's task list. By default excludes completed todos (status='done')
    to show only active work. Todos with no status set are considered incomplete and
    are included by default.

    Args:
        context: Filter to todos in this context/project only.
        status: Filter by specific status: active, done, blocked, or archive.
                If specified, returns only todos with this exact status.
                Overrides include_done.
        include_done: If True, include all todos regardless of status.
                      Default: False (excludes status='done' only).

    Returns:
        List of todos ordered by priority (1=highest first, nulls last) then by
        modification date (newest first). Each contains: id, content, context,
        kind, created, modified, and optional priority, status, tags.
        Returns {"error": "..."} if status parameter is invalid.
    """
    if status is not None and status not in VALID_STATUSES:
        return {"error": f"Invalid status '{status}'. Must be one of: {', '.join(VALID_STATUSES)}"}

    @sync_to_async
    def fetch():
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

        qs = qs.order_by(
            models.F('priority').asc(nulls_last=True),
            '-timestamp_modified'
        )

        return [_format_entry(entry) for entry in qs]

    return await fetch()


@mcp.tool()
async def get_memories(
    context: str = None,
    limit: int = 50,
) -> list:
    """
    Get memory entries - general notes and information.

    Memories are the default entry type for storing facts, notes, and
    information the user wants to remember.

    Args:
        context: Filter to memories in this context/project.
        limit: Maximum results to return. Default: 50.

    Returns:
        List of memories ordered by modification date (newest first),
        each containing: content, context, created, modified, tags.

    OUTPUT FORMAT: When presenting to user, show each memory as:
        - CONTENT
    Group by context if multiple contexts present.
    """
    if not isinstance(limit, int) or limit < 1:
        return {"error": f"limit must be a positive integer, got {limit}"}

    @sync_to_async
    def fetch():
        qs = Entry.objects.filter(
            kind='memory',
            deleted_at__isnull=True,
        ).select_related('context').prefetch_related('tags')

        if context:
            qs = qs.filter(context__name=context)

        qs = qs.order_by('-timestamp_modified')[:limit]

        return [_format_entry(entry) for entry in qs]

    return await fetch()


@mcp.tool()
async def search_entries(
    query: str,
    kind: str = None,
    context: str = None,
    limit: int = 50,
) -> list:
    """
    Full-text search across entries.

    Performs case-insensitive search in entry content. Use this to find specific
    information, locate entries by keyword, or explore what the user has stored
    on a topic.

    Args:
        query: Search term to find in entry content (case-insensitive substring match).
        kind: Filter to specific entry type: memory, todo, journal, profile,
              ai, bookmark, or list.
        context: Filter to entries in this context/project only.
        limit: Maximum results to return. Default: 50. Set higher if needed.

    Returns:
        List of matching entries ordered by modification date (newest first),
        each containing: id, content, kind, context, created, modified, and
        optional name, priority, status, tags.
        Returns {"error": "..."} if parameters are invalid.
    """
    if not query:
        return {"error": "query is required"}

    if kind is not None and kind not in VALID_KINDS:
        return {"error": f"Invalid kind '{kind}'. Must be one of: {', '.join(VALID_KINDS)}"}

    if not isinstance(limit, int) or limit < 1:
        return {"error": f"limit must be a positive integer, got {limit}"}

    @sync_to_async
    def fetch():
        qs = Entry.objects.filter(
            content__icontains=query,
            deleted_at__isnull=True,
        ).select_related('context').prefetch_related('tags')

        if kind:
            qs = qs.filter(kind=kind)
        if context:
            qs = qs.filter(context__name=context)

        qs = qs.order_by('-timestamp_modified')[:limit]

        return [_format_entry(entry) for entry in qs]

    return await fetch()


@mcp.tool()
async def get_entry(entry_id: str) -> dict:
    """
    Get a single entry by ID.

    Use this to look up an entry before deletion or to inspect entry details.

    Args:
        entry_id: The UUID of the entry to retrieve (required).

    Returns:
        Full entry with all fields: id, content, kind, context, created, modified,
        and optional name, priority, status, tags.
        Returns {"error": "..."} if entry not found.
    """
    if not entry_id:
        return {"error": "entry_id is required"}

    @sync_to_async
    def fetch():
        entry = Entry.objects.select_related('context').filter(
            id=entry_id,
            deleted_at__isnull=True,
        ).prefetch_related('tags').first()

        if not entry:
            return {"error": f"Entry '{entry_id}' not found"}

        return _format_entry(entry)

    return await fetch()


@mcp.tool()
async def delete_entry(entry_id: str, content: str) -> dict:
    """
    Delete an entry from the user's tjai knowledge base.

    Performs a soft delete by marking the entry as deleted. The entry can
    potentially be recovered but will no longer appear in queries.

    IMPORTANT: Before calling this, use get_entry to fetch the entry content.
    The content parameter must match the entry's actual content - this ensures
    the user sees what will be deleted in the approval prompt.

    Args:
        entry_id: The UUID of the entry to delete (required).
        content: The entry's content text (required). Must match actual content.

    Returns:
        Confirmation with deleted entry's full content.
        Returns {"error": "..."} if entry not found, already deleted, or content mismatch.
    """
    if not entry_id:
        return {"error": "entry_id is required"}
    if not content:
        return {"error": "content is required - use get_entry first to fetch content"}

    @sync_to_async
    def do_delete():
        entry = Entry.objects.select_related('context').filter(
            id=entry_id,
            deleted_at__isnull=True,
        ).prefetch_related('tags').first()

        if not entry:
            return {"error": f"Entry '{entry_id}' not found or already deleted"}

        # Strip punctuation for comparison (MCP may alter unicode punctuation)
        import re
        def strip_punct(s):
            return re.sub(r'[^\w\s]', '', s)

        if strip_punct(entry.content) != strip_punct(content):
            return {"error": "Content does not match entry. Use get_entry to fetch current content."}

        now = time.time()
        entry.deleted_at = now
        entry.timestamp_modified = now
        entry.is_dirty = 1
        entry.save(update_fields=['deleted_at', 'timestamp_modified', 'is_dirty'])

        return {"deleted": True, "entry": _format_entry(entry)}

    return await do_delete()


@mcp.tool()
async def edit_entry(
    entry_id: str,
    content: str,
    context: str = None,
    clear_context: bool = False,
    tags: list[str] = None,
    event_date: str = None,
    event_time: str = None,
    clear_event_date: bool = False,
    priority: int = None,
    clear_priority: bool = False,
    status: str = None,
    clear_status: bool = False,
    name: str = None,
    clear_name: bool = False,
    keep_time: bool = False,
) -> dict:
    """
    Edit an existing entry in the user's tjai knowledge base.

    Updates the content and optionally other fields of an existing entry.
    The entry must exist and not be deleted.

    Args:
        entry_id: The UUID of the entry to edit (required).
        content: The new content text (required). Shows user the final result.
        context: Set the entry's context to this value. Must be an existing context.
        clear_context: If True, removes the entry's context.
        tags: Replace all tags with this list. Pass [] to remove all tags.
        event_date: Set event date in YYYYMMDD format (for journal entries).
        event_time: Set event time in HHMM format (e.g., "0900", "1430").
        clear_event_date: If True, removes the event date.
        priority: Set priority level (positive integer, 1=highest).
        clear_priority: If True, removes priority.
        status: Set status. One of: active, done, blocked, archive.
        clear_status: If True, removes status.
        name: Set unique identifier for easy reference.
        clear_name: If True, removes name.
        keep_time: If True, preserve original modification timestamp.

    Returns:
        The updated entry with all fields.
        Returns {"error": "..."} if validation fails.
    """
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

    @sync_to_async
    def do_edit():
        entry = Entry.objects.select_related('context').filter(
            id=entry_id,
            deleted_at__isnull=True,
        ).prefetch_related('tags').first()

        if not entry:
            return {"error": f"Entry '{entry_id}' not found or already deleted"}

        # Handle content - extract time if event_date set and no event_time
        actual_content = content
        hour, minute = None, None
        if event_date and not event_time:
            actual_content, hour, minute = _extract_time_from_content(content)

        entry.content = actual_content

        # Context
        if context is not None:
            try:
                context_obj = Context.objects.get(name=context)
                entry.context = context_obj
            except Context.DoesNotExist:
                return {"error": f"Context '{context}' does not exist. Use list_contexts() to see valid contexts."}
        elif clear_context:
            entry.context = None

        # Tags
        if tags is not None:
            entry.tags.all().delete()
            for tag_name in tags:
                if tag_name and tag_name.strip():
                    Tag.objects.create(tag_name=tag_name.strip(), entry=entry)

        # Event date/time
        if event_date or clear_event_date:
            if entry.data is None:
                entry.data = {}
            if clear_event_date:
                entry.data.pop('event_date', None)
            else:
                tz_config = SysConfig.objects.filter(key='timezone').first()
                if not tz_config:
                    return {"error": "SysConfig 'timezone' not set"}
                tz = ZoneInfo(tz_config.value)
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

        # Priority
        if priority is not None:
            entry.priority = priority
        elif clear_priority:
            entry.priority = None

        # Status
        if status is not None:
            entry.status = status
        elif clear_status:
            entry.status = None

        # Name
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

        return _format_entry(entry)

    return await do_edit()
