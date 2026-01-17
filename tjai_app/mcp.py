"""
MCP (Model Context Protocol) tools for tjai.

Provides AI assistants with access to:
- Calendar/journal entries
- Profile facts about the user
- AI guidance (general and context-specific)
- Entry creation
- Context listing
"""

import time
import uuid
from datetime import datetime, timedelta

from asgiref.sync import sync_to_async
from django.utils import timezone
from mcp_server import mcp_server as mcp

from .models import Entry, Context, Tag


def _parse_date(date_str: str):
    """Parse date string to datetime. Supports ISO format and YYYYMMDD."""
    if not date_str:
        return None
    try:
        # Try ISO format first
        if 'T' in date_str or '-' in date_str:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        # Try YYYYMMDD format
        if len(date_str) == 8 and date_str.isdigit():
            return datetime.strptime(date_str, '%Y%m%d')
        return None
    except (ValueError, TypeError):
        return None


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
    if entry.priority:
        result["priority"] = entry.priority
    if entry.status:
        result["status"] = entry.status
    if entry.data:
        result["data"] = entry.data
    # Include tags
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

    Journal entries are date-specific events, appointments, and notes.
    By default returns entries for the next 30 days.

    Args:
        start_date: Start date (ISO format or YYYYMMDD). Default: today.
        end_date: End date (ISO format or YYYYMMDD). Default: start + 30 days.
        context: Filter to entries in this context/project.
        days: Alternative to end_date - number of days from start_date.

    Returns list of journal entries with: id, content, event_date, context, tags.
    """
    @sync_to_async
    def fetch():
        # Parse dates
        start = _parse_date(start_date)
        if not start:
            start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

        if days:
            end = start + timedelta(days=days)
        elif end_date:
            end = _parse_date(end_date)
            if not end:
                end = start + timedelta(days=30)
        else:
            end = start + timedelta(days=30)

        # Query journal entries
        qs = Entry.objects.filter(
            kind='journal',
            deleted_at__isnull=True,
        ).select_related('context').prefetch_related('tags')

        # Filter by context if specified
        if context:
            qs = qs.filter(context__name=context)

        # Filter by event_date in data field
        # Journal entries store event_date as YYYYMMDD in data.event_date
        start_str = start.strftime('%Y%m%d')
        end_str = end.strftime('%Y%m%d')

        results = []
        for entry in qs:
            event_date = None
            if entry.data and 'event_date' in entry.data:
                event_date = entry.data.get('event_date')
            if event_date and start_str <= event_date <= end_str:
                result = _format_entry(entry)
                result['event_date'] = event_date
                results.append(result)

        # Sort by event_date
        results.sort(key=lambda x: x.get('event_date', ''))
        return results

    return await fetch()


@mcp.tool()
async def get_profile() -> list:
    """
    Get all profile entries about the user.

    Profile entries contain personal facts, preferences, and information
    that AI assistants should know about the user. Use this to understand
    context about who you're helping.

    Returns list of profile entries with: id, content, context, tags.
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
    Get AI guidance entries - instructions for AI assistants.

    AI guidance entries contain behavioral instructions, preferences,
    and guidelines that AI assistants should follow. These can be:
    - General (no context) - apply to all interactions
    - Context-specific - apply when working on that project/topic

    IMPORTANT: Always call this before starting work on a context to get
    project-specific instructions.

    Args:
        context: Filter to guidance for this context. If None, returns
                 general guidance (entries with no context) plus any
                 context-specific guidance.

    Returns list of AI guidance entries with: id, content, context, tags.
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

            # If context filter specified, return general + that context
            if context:
                if entry_context is None or entry_context == context:
                    results.append(_format_entry(entry))
            else:
                # No filter - return all
                results.append(_format_entry(entry))

        return results

    return await fetch()


@mcp.tool()
async def list_contexts() -> list:
    """
    List all available contexts (projects/topics).

    Contexts group entries by project or topic. Use this to understand
    what projects exist and to filter other queries.

    Returns list of contexts with: name, title, description, entry_count.
    """
    @sync_to_async
    def fetch():
        from django.db.models import Count

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

    from django.db import models
    return await fetch()


@mcp.tool()
async def create_entry(
    content: str,
    kind: str = "memory",
    context: str = None,
    name: str = None,
    tags: str = None,
    event_date: str = None,
    priority: int = None,
    status: str = None,
) -> dict:
    """
    Create a new entry in tjai.

    Use this to add information to the user's personal knowledge base.

    Args:
        content: The entry content (required).
        kind: Entry type - one of: memory, todo, journal, profile, ai, bookmark.
              Default: memory.
        context: Context/project name to associate with. Creates context if needed.
        name: Optional unique name for easy reference (must be unique within context).
        tags: Comma-separated tags to add (e.g., "important,followup").
        event_date: For journal entries - the date (YYYYMMDD format).
        priority: Priority level (1=highest). For todos.
        status: Status value (active, done, blocked). For todos.

    Returns the created entry with its id.
    """
    @sync_to_async
    def create():
        now = time.time()

        # Validate kind
        valid_kinds = ['memory', 'todo', 'journal', 'profile', 'ai', 'bookmark', 'list']
        if kind not in valid_kinds:
            return {"error": f"Invalid kind '{kind}'. Must be one of: {', '.join(valid_kinds)}"}

        # Get or create context if specified
        context_obj = None
        if context:
            context_obj, _ = Context.objects.get_or_create(
                name=context,
                defaults={
                    'timestamp_created': now,
                    'timestamp_modified': now,
                }
            )

        # Build data field
        data = {}
        if event_date:
            data['event_date'] = event_date
        if not data:
            data = None

        # Create entry
        entry = Entry.objects.create(
            id=str(uuid.uuid4()),
            content=content,
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

        # Add tags
        if tags:
            tag_list = [t.strip() for t in tags.split(',') if t.strip()]
            for tag_name in tag_list:
                Tag.objects.create(tag_name=tag_name, entry=entry)

        return _format_entry(entry)

    return await create()


@mcp.tool()
async def get_todos(
    context: str = None,
    status: str = None,
    include_done: bool = False,
) -> list:
    """
    Get todo entries.

    Args:
        context: Filter to todos in this context/project.
        status: Filter by status (active, done, blocked, archive).
        include_done: If True, include completed todos. Default: False.

    Returns list of todos with: id, content, context, status, priority, tags.
    """
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

        # Order by priority (nulls last), then by modified date
        qs = qs.order_by(
            models.F('priority').asc(nulls_last=True),
            '-timestamp_modified'
        )

        return [_format_entry(entry) for entry in qs[:100]]

    from django.db import models
    return await fetch()


@mcp.tool()
async def search_entries(
    query: str,
    kind: str = None,
    context: str = None,
    limit: int = 50,
) -> list:
    """
    Search entries by content.

    Args:
        query: Search term to find in entry content.
        kind: Filter to specific entry type (memory, todo, journal, etc.).
        context: Filter to entries in this context.
        limit: Maximum results to return. Default: 50.

    Returns list of matching entries.
    """
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
