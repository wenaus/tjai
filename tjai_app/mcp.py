"""
MCP (Model Context Protocol) tools for tjai.

tjai is a personal AI memory and task management system. These tools provide
AI assistants with structured access to the user's knowledge base.

Available tools:
    get_calendar      - Retrieve journal entries for a date range (events, appointments)
    get_profile       - Get personal facts and preferences about the user
    get_ai_guidance   - Get behavioral instructions for AI assistants
    list_contexts     - List all projects/topics for organizing entries
    create_entry      - Add new entries (memories, todos, journal, profile, ai, bookmark, action)
    get_todos         - Retrieve todo items with filtering options
    get_memories      - Get memory entries. Call unfiltered to see general activity
    get_bookmarks     - Get saved bookmark entries (URLs)
    search_entries    - Full-text search across all entries
    get_entry         - Get a single entry by ID
    edit_entry        - Edit an existing entry
    delete_entry      - Soft delete an entry (requires user approval)

Entry types: memory, todo, journal, profile, bookmark, ai, list, action

Contexts group entries by project or topic. Most tools accept a context parameter
to filter results. Use get_ai_guidance(context) before starting work on any
project to get project-specific instructions.

Error handling: Tools return {"error": "message"} on validation failures.
Check for "error" key in response before processing results.
"""

from asgiref.sync import sync_to_async
from mcp_server import mcp_server as mcp

from . import services


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
    return await sync_to_async(services.get_calendar)(
        start_date=start_date, end_date=end_date, context=context, days=days,
    )


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
    return await sync_to_async(services.get_profile)()


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
    return await sync_to_async(services.get_ai_guidance)(context=context)


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
    return await sync_to_async(services.list_contexts)()


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
    data: dict = None,
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
              ai (instructions for AI assistants), bookmark (URLs), list (lists),
              action (automated actions with trigger config).
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
        data: JSON metadata object. For journal entries, event_date/event_time are
              stored here automatically. For action entries, holds trigger config
              (trigger, interval_hours, mechanical_script, ai_prompt, last_run).

    Example for calendar event "Meeting at 9am on Jan 28, 2026":
        create_entry(content="Meeting", kind="journal", event_date="20260128", event_time="0900")

    Returns:
        The created entry with id, content, kind, context, created, modified,
        and any optional fields (name, priority, status, tags, data).
        Returns {"error": "..."} if validation fails.
    """
    return await sync_to_async(services.create_entry)(
        content=content, kind=kind, context=context, name=name, tags=tags,
        event_date=event_date, event_time=event_time, priority=priority,
        status=status, create_context=create_context, source_tags=['fromai'],
        data=data,
    )


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
    return await sync_to_async(services.get_todos)(
        context=context, status=status, include_done=include_done,
    )


@mcp.tool()
async def get_memories(
    context: str = None,
    limit: int = 50,
    start_date: str = None,
    end_date: str = None,
) -> list:
    """
    Get memory entries - general notes and information.

    Memories are the default entry type for storing facts, notes, and
    information the user wants to remember. Dialog turns are also memories.

    IMPORTANT: To review recent activity, call get_memories(limit=20) with
    NO filters first. Do not add context, date, or other filters unless the
    user specifically asks for them. Unfiltered recent memories show what has
    actually been happening — filtering defeats that purpose.

    Args:
        context: Filter to memories in this context/project.
        limit: Maximum results to return. Default: 50.
        start_date: Start of date range (YYYYMMDD, ISO format, or natural language
                    like '7d', 'yesterday', 'monday'). Default: no filtering.
        end_date: End of date range. Default: no filtering.

    Returns:
        List of memories ordered by modification date (newest first),
        each containing: content, context, created, modified, tags.

    OUTPUT FORMAT: When presenting to user, show each memory as:
        - CONTENT
    Group by context if multiple contexts present.
    """
    return await sync_to_async(services.get_memories)(
        context=context, limit=limit, start_date=start_date, end_date=end_date,
    )


@mcp.tool()
async def get_bookmarks(
    context: str = None,
    limit: int = 50,
    start_date: str = None,
    end_date: str = None,
) -> list:
    """
    Get saved bookmark entries (URLs).

    Bookmarks are URLs and references the user has saved. May include
    markdown-formatted links with titles.

    Args:
        context: Filter to bookmarks in this context/project.
        limit: Maximum results to return. Default: 50.
        start_date: Start of date range (YYYYMMDD, ISO format, or natural language
                    like '7d', 'yesterday', 'monday'). Default: no filtering.
        end_date: End of date range. Default: no filtering.

    Returns:
        List of bookmark entries ordered by modification date (newest first),
        each containing: id, content, kind, context, created, modified, tags.
    """
    return await sync_to_async(services.get_bookmarks)(
        context=context, limit=limit, start_date=start_date, end_date=end_date,
    )


@mcp.tool()
async def search_entries(
    query: str,
    kind: str = None,
    context: str = None,
    limit: int = 50,
    start_date: str = None,
    end_date: str = None,
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
        start_date: Start of date range (YYYYMMDD, ISO format, or natural language
                    like '7d', 'yesterday', 'monday'). Default: no filtering.
        end_date: End of date range. Default: no filtering.

    Returns:
        List of matching entries ordered by modification date (newest first),
        each containing: id, content, kind, context, created, modified, and
        optional name, priority, status, tags.
        Returns {"error": "..."} if parameters are invalid.
    """
    return await sync_to_async(services.search_entries)(
        query=query, kind=kind, context=context, limit=limit,
        start_date=start_date, end_date=end_date,
    )


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
    return await sync_to_async(services.get_entry)(entry_id=entry_id)


@mcp.tool()
async def get_entry_by_entry_id(entry_id: str) -> dict:
    """
    Find an entry by its human-readable entry_id (stored in data.entry_id).

    Use this instead of get_entry when you have a readable identifier like
    'daily-2026-02-23' or 'history-selection-guidance' rather than a UUID.

    Args:
        entry_id: The human-readable entry_id (e.g., 'daily-2026-02-23').

    Returns:
        Full entry with all fields including the UUID id.
        Returns {"error": "..."} if not found.
    """
    return await sync_to_async(services.get_entry_by_entry_id)(entry_id=entry_id)


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
    data: dict = None,
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
        data: JSON metadata to merge into the entry's data field. Keys with
              null values are removed. Merges with existing data (does not replace).

    Returns:
        The updated entry with all fields.
        Returns {"error": "..."} if validation fails.
    """
    return await sync_to_async(services.edit_entry)(
        entry_id=entry_id, content=content, context=context,
        clear_context=clear_context, tags=tags, event_date=event_date,
        event_time=event_time, clear_event_date=clear_event_date,
        priority=priority, clear_priority=clear_priority,
        status=status, clear_status=clear_status,
        name=name, clear_name=clear_name, keep_time=keep_time,
        data=data,
    )


@mcp.tool()
async def run_action(entry_id: str) -> dict:
    """
    Execute a specific action entry immediately.

    Runs the action's full pipeline: mechanical script, journal entry,
    AI dispatch, and updates last_run. Returns execution result.

    Args:
        entry_id: The UUID of the action entry to execute (required).

    Returns:
        Result dict with success status, action content, and entry_id.
        Returns {"error": "..."} if entry not found or execution fails.
    """
    return await sync_to_async(services.run_action)(entry_id=entry_id)


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
    return await sync_to_async(services.delete_entry)(
        entry_id=entry_id, content=content,
    )
