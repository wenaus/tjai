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
    get_dialog        - Get recorded human-AI dialog turns for a host/time range
    get_logs          - Read application log (AppLog) rows — agent/script/server logs
    search_entries    - Search/list entries with optional full-text query and filters
    get_named_entries - Get entries by @name, or list all named entries
    get_entry         - Get a single entry by ID
    get_entry_by_entry_id - Find entry by human-readable entry_id
    get_entry_versions    - Get automatic version history for an entry
    edit_entry_metadata    - Edit an entry's metadata (tags, status, priority, …)
    replace_entry_content  - Replace an entry's content (destructive rewrite)
    append_entry_content   - Append text to an entry's existing content
    run_action        - Execute an action entry immediately
    copy_calendar_entry - Copy a journal entry to a new date (preserves all fields)
    change_entry_kind - Change an entry's type without modifying content or timestamp
    delete_entry      - Soft delete an entry (requires user approval)
    create_goal       - Create a goal entry (convenience wrapper for create_entry)
    get_goal          - Get a goal entry with all its relations
    create_relation   - Create a relation between any two entries
    edit_relation     - Edit a relation's type and/or data
    delete_relation   - Delete a relation
    get_relations     - Get all relations for an entry
    get_relation_graph - Traverse the relation graph from an entry

Entry types: memory, todo, journal, profile, bookmark, ai, list, action, goal
Valid statuses: active, done, blocked, archive. Priority: positive integers (1=highest).

IMPORTANT — entry_id: Every non-trivial entry MUST have a human-readable entry_id
set via data={"entry_id": "kebab-case-slug"}. This is how entries are referenced,
linked, and looked up (get_entry_by_entry_id). Without it, the entry is UUID-only.

Contexts group entries by project or topic. Most tools accept a context parameter
to filter results. Use get_ai_guidance(context) before starting work on any
project to get project-specific instructions.

MCP output compatibility: Read tools whose natural result is a list return
JSON text rather than a top-level MCP array, so empty results are delivered as
the literal string "[]" instead of zero content blocks. Parse the returned text
as JSON before processing.

Error handling: Tools return {"error": "message"} on validation failures.
Always check for "error" key in response before processing results.

Pagination contract: list-style read tools that accept `limit` and `offset`
use `limit` as a page size, not a requirement. The hard maximum page size is
500. If a returned list has exactly the requested limit, more results may
exist; at your discretion, fetch the next page with `offset += limit` until a
page returns fewer than the requested limit or the requested window is complete.
"""

import json

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import services
from .services import DEFAULT_MAX_CONTENT_LENGTH


_mcp_config = settings.TJAI_MCP_SERVER_CONFIG
_allowed_hosts = [
    host
    for host in settings.ALLOWED_HOSTS
    if host and host != "*"
] + [
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
]
mcp = FastMCP(
    name=_mcp_config["name"],
    instructions=_mcp_config["instructions"],
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(allowed_hosts=_allowed_hosts),
)


def _json_text(value) -> str:
    """Return MCP-safe JSON text, including for empty lists."""
    return json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False)


@mcp.tool()
async def get_server_instructions() -> str:
    """
    Get the tjai MCP server instructions.

    Compatibility tool for clients and permissions lists that previously used
    django-mcp-server's server-instruction helper.
    """
    return _mcp_config["instructions"]


@mcp.tool()
async def get_calendar(
    start_date: str = None,
    end_date: str = None,
    context: str = None,
    days: int = None,
) -> str:
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
    result = await sync_to_async(services.get_calendar)(
        start_date=start_date, end_date=end_date, context=context, days=days,
    )
    return _json_text(result)


@mcp.tool()
async def get_profile() -> str:
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
    result = await sync_to_async(services.get_profile)()
    return _json_text(result)


@mcp.tool()
async def get_ai_guidance(context: str = None, location_name: str = None) -> str:
    """
    Get AI guidance entries - behavioral instructions for AI assistants.

    AI guidance entries define how AI assistants should behave. They include:
    - General guidance (no context): Universal rules applying to all interactions
    - Context-specific guidance: Rules for working on particular projects/topics
    - Machine-specific guidance: Rules/facts for a particular host, keyed by
      location_name (see below)

    IMPORTANT: Always call this before starting work on any context/project to
    get project-specific instructions. The user expects you to follow these.

    Args:
        context: If provided, returns general guidance PLUS guidance specific
                 to this context. If None, returns general guidance only
                 (entries with no context). To enumerate context-specific
                 guidance, call list_contexts() and then make per-context
                 calls.
        location_name: If provided, additionally returns the machine-details
                 entry named `<location_name>_details` (kind='memory',
                 context=null, data.entry_id='<location_name>_details').
                 Machine-details are facts about a machine (deployed apps,
                 working dirs, ingress paths, collaboration axes) — not AI
                 behavioral rules — so they are kind='memory', not 'ai'. The
                 SessionStart hook reads `location_name` from the local
                 `~/.tjai/config.json` and passes it here. If no such entry
                 exists, an info notice is appended to the results telling
                 you to surface that fact to the user so the missing entry
                 can be authored.

    Returns:
        List of AI guidance entries ordered by context then modification date,
        each containing: id, content, context (null for general), kind,
        created, modified, tags. When location_name is supplied, the
        machine-specific entry (or an info notice if absent) is appended.
    """
    result = await sync_to_async(services.get_ai_guidance)(
        context=context, location_name=location_name
    )
    return _json_text(result)


@mcp.tool()
async def list_contexts() -> str:
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
    result = await sync_to_async(services.list_contexts)()
    return _json_text(result)


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
        data: JSON metadata object. IMPORTANT: Always include an "entry_id" key
              with a human-readable slug (e.g., {"entry_id": "research-css-design"}).
              This is the primary way entries are referenced and linked — without it,
              the entry is only findable by UUID. Use lowercase-kebab-case.
              For journal entries, event_date/event_time are stored here automatically.
              For action entries, holds trigger config
              (trigger, interval_hours, mechanical_script, ai_prompt, last_run).

    ENTRY_ID IS REQUIRED: Every non-trivial entry MUST have data.entry_id set.
    It is the human-readable identifier used in URLs, cross-references (rel_goal),
    and the get_entry_by_entry_id() lookup. Omitting it forces UUID-only access.

    Example for calendar event "Meeting at 9am on Jan 28, 2026":
        create_entry(content="Meeting", kind="journal", event_date="20260128",
                     event_time="0900", data={"entry_id": "meeting-hsf-20260128"})

    Example for a research topic:
        create_entry(content="Why LLMs fail at CSS", kind="memory",
                     tags="research_topic", data={"entry_id": "research-css-llm-failure"})

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
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
) -> str:
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
    result = await sync_to_async(services.get_todos)(
        context=context, status=status, include_done=include_done,
        max_content_length=max_content_length,
    )
    return _json_text(result)


@mcp.tool()
async def get_memories(
    context: str = None,
    limit: int = 50,
    offset: int = 0,
    start_date: str = None,
    end_date: str = None,
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
) -> str:
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
        limit: Page size. Default: 50. Hard maximum: 500.
               If the result count equals limit, more may exist.
        offset: Zero-based result offset for pagination. Default: 0.
                At your discretion, fetch the next page with offset += limit.
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
    result = await sync_to_async(services.get_memories)(
        context=context, limit=limit, offset=offset, start_date=start_date,
        end_date=end_date, max_content_length=max_content_length,
    )
    return _json_text(result)


@mcp.tool()
async def get_dialog(
    host: str,
    start_date: str = None,
    end_date: str = None,
    limit: int = None,
    offset: int = 0,
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
) -> str:
    """
    Get AI assistant dialog turns for a host and time range.

    Retrieves recorded human-AI dialog turns ordered chronologically.
    Use this to review prior session dialog for continuity or assessment.

    To get your local hostname: read ~/.tjai/config.json field "location_name".

    Args:
        host: Hostname to retrieve dialog for (e.g. 'ec2dev', 'MacbookPro'),
              or 'all' for all hosts.
        start_date: Start of date range (YYYYMMDD, ISO format, or natural language
                    like '1d', '7d', 'yesterday', 'monday'). Required.
        end_date: End of date range. Optional — defaults to now.
        limit: Optional page size. Hard maximum: 500. If omitted, returns all
               matching dialog turns.
        offset: Zero-based result offset for pagination. Default: 0.
                Use with limit to fetch subsequent pages at your discretion.
        max_content_length: Truncate content to this many chars. Default: 500.
                           Pass 0 for full content (use sparingly on large ranges).

    Returns:
        List of dialog turns ordered chronologically, each containing:
        timestamp, role (user/assistant), speaker, speaker_type
        (human/ai/unknown), client, model, hostname, content.
    """
    result = await sync_to_async(services.get_dialog)(
        host=host, start_date=start_date, end_date=end_date,
        limit=limit, offset=offset, max_content_length=max_content_length,
    )
    return _json_text(result)


@mcp.tool()
async def get_logs(
    source: str = None,
    level: str = None,
    contains: str = None,
    ref: str = None,
    start_date: str = None,
    end_date: str = None,
    limit: int = 100,
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
) -> str:
    """
    Read application log entries (the AppLog / `applog` table) — the log lines
    scripts, agents, and the server emit via DbLogHandler. Same data as the
    web Agent Log page.

    Use this for operational/diagnostic questions ("did reconcile_research
    run?", "any ERRORs from the action agent today?") INSTEAD of dropping to
    raw SQL. AppLog is not an Entry — search_entries/get_memories cannot reach
    it.

    Args:
        source: Exact log source, e.g. 'reconcile_research', 'agent_complete',
                'action_agent', 'watchdog'.
        level: Minimum level (inclusive) — DEBUG|INFO|WARNING|ERROR|CRITICAL.
        contains: Case-insensitive substring match on the message.
        ref: Filter to a referenced entry_id/action_id (matched in extra_data
             or the message).
        start_date: Start of range (YYYYMMDD, ISO, or natural language like
                    '1d', '6h', 'yesterday', 'monday'). Default: no filtering.
        end_date: End of range. Default: now.
        limit: Page size. Default 100, hard maximum 500.
        max_content_length: Truncate each message to this many chars
                            (default 500; 0 for full).

    Returns:
        Newest-first list of entries, each containing:
        timestamp (ET), level, source, message, extra_data.
    """
    result = await sync_to_async(services.get_logs)(
        source=source, level=level, contains=contains, ref=ref,
        start_date=start_date, end_date=end_date, limit=limit,
        max_content_length=max_content_length,
    )
    return _json_text(result)


@mcp.tool()
async def get_bookmarks(
    context: str = None,
    limit: int = 50,
    offset: int = 0,
    start_date: str = None,
    end_date: str = None,
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
) -> str:
    """
    Get saved bookmark entries (URLs).

    Bookmarks are URLs and references the user has saved. May include
    markdown-formatted links with titles.

    Args:
        context: Filter to bookmarks in this context/project.
        limit: Page size. Default: 50. Hard maximum: 500.
               If the result count equals limit, more may exist.
        offset: Zero-based result offset for pagination. Default: 0.
                At your discretion, fetch the next page with offset += limit.
        start_date: Start of date range (YYYYMMDD, ISO format, or natural language
                    like '7d', 'yesterday', 'monday'). Default: no filtering.
        end_date: End of date range. Default: no filtering.

    Returns:
        List of bookmark entries ordered by modification date (newest first),
        each containing: id, content, kind, context, created, modified, tags.
    """
    result = await sync_to_async(services.get_bookmarks)(
        context=context, limit=limit, offset=offset, start_date=start_date,
        end_date=end_date, max_content_length=max_content_length,
    )
    return _json_text(result)


@mcp.tool()
async def search_entries(
    query: str = None,
    kind: str = None,
    context: str = None,
    limit: int = 50,
    offset: int = 0,
    start_date: str = None,
    end_date: str = None,
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
    order_by: str = 'time',
) -> str:
    """
    Search or list entries.

    When query is non-empty, uses PostgreSQL full-text search with stemming,
    relevance ranking, and Google-style syntax (quoted phrases, -exclusions).
    When query is omitted or empty, lists entries matching the structured
    filters only. This is the correct mode for requests like "recent goals" or
    "todos modified last night" where invented keywords would create false
    negatives.

    Args:
        query: Optional search terms. Supports Google-style syntax: quoted phrases
               ("streaming workflow"), exclusions (-test), and boolean AND/OR.
               Stemming is automatic: "computing" matches "computed", "computation".
        kind: Filter to specific entry type: memory, todo, journal, profile,
              ai, bookmark, or list.
        context: Filter to entries in this context/project only.
        limit: Page size. Default: 50. Hard maximum: 500.
               If the result count equals limit, more may exist.
        offset: Zero-based result offset for pagination. Default: 0. To fetch
                another page at your discretion, call again with offset += limit.
        start_date: Start of date range (YYYYMMDD, ISO format, or natural language
                    like '7d', 'yesterday', 'monday'). Default: no filtering.
        end_date: End of date range. Default: no filtering.
        max_content_length: Truncate content to this many characters (appends …).
                           Default: 500. Set 0 for full content. Use get_entry() for
                           full content of specific entries.
        order_by: Sort order. 'time' (default) = newest first by modification date.
                  'rank' = best match first by search relevance and requires a
                  non-empty query. 'size' = longest content first.

    Returns:
        List of matching entries, each containing: id, content (preview), kind,
        context, created, modified, and optional name, priority, status, tags.
        If the number of results equals limit, more results may exist; call
        again with a larger offset to continue.
        Returns {"error": "..."} if parameters are invalid.
    """
    result = await sync_to_async(services.search_entries)(
        query=query, kind=kind, context=context, limit=limit, offset=offset,
        start_date=start_date, end_date=end_date,
        max_content_length=max_content_length,
        order_by=order_by,
    )
    return _json_text(result)


@mcp.tool()
async def get_named_entries(
    name: str = None,
    context: str = None,
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
) -> str:
    """
    Get entries that have an @name assigned.

    Named entries are user-curated singletons with unique identifiers
    (e.g., @shopping, @budget, @tjai). Use this to look up a specific
    named entry or to list all named entries.

    Args:
        name: If provided, return the entry with this exact @name.
              If omitted, return all named entries.
        context: Filter to entries in this context/project only.

    Returns:
        If name provided: single entry dict with all fields.
        If name omitted: list of all named entries, sorted alphabetically by name.
        Returns {"error": "..."} if named entry not found.
    """
    result = await sync_to_async(services.get_named_entries)(
        name=name, context=context, max_content_length=max_content_length,
    )
    return _json_text(result)


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
async def edit_entry_metadata(
    entry_id: str,
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
    Edit metadata fields of an existing entry WITHOUT touching its content.

    For content changes use replace_entry_content (full replace) or
    append_entry_content (add to existing). This tool has no `content`
    parameter so the content-vs-metadata distinction is unmissable.

    Args:
        entry_id: The UUID of the entry to edit (required).
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
    return await sync_to_async(services.edit_entry_metadata)(
        entry_id=entry_id, context=context,
        clear_context=clear_context, tags=tags, event_date=event_date,
        event_time=event_time, clear_event_date=clear_event_date,
        priority=priority, clear_priority=clear_priority,
        status=status, clear_status=clear_status,
        name=name, clear_name=clear_name, keep_time=keep_time,
        data=data,
    )


@mcp.tool()
async def replace_entry_content(entry_id: str, content: str) -> dict:
    """
    Replace an entry's content with new text. DESTRUCTIVE — the previous
    content is gone from the current version.

    The previous content remains in version history (see get_entry_versions /
    restore_version) and can be recovered if this was a mistake. Still: use
    append_entry_content when you want to ADD to existing content; use this
    only for deliberate full rewrites.

    For metadata-only edits use edit_entry_metadata.

    Args:
        entry_id: The UUID of the entry to edit (required).
        content: The new content text (required, non-empty).

    Returns:
        The updated entry with all fields and a complete unified `diff`.
        After a successful edit, you MUST present the returned `diff` verbatim
        to the user in a fenced diff block. Never silently omit or summarize it.
        Returns {"error": "..."} if validation fails.
    """
    return await sync_to_async(services.replace_entry_content)(
        entry_id=entry_id, content=content,
    )


@mcp.tool()
async def replace_text_in_entry(
    entry_id: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    expected_modified_at: str = None,
) -> dict:
    """
    Surgical exact-match replace within an entry's content. Replaces
    `old_text` with `new_text`. Errors if `old_text` is absent, or if it
    occurs more than once and `replace_all` is False (supply more
    surrounding context to disambiguate, or set `replace_all=True`).

    PREFER THIS over `replace_entry_content` whenever you only want to
    change a small portion of a long entry — `replace_entry_content`
    forces you to re-emit the entire body, which is dominated by output
    token cost. Surgical edits send only the change. Same pattern as
    Claude Code's `Edit` tool.

    Each call creates a new entry version (existing version-history /
    restore_version semantics). Atomic: succeeds or no change.

    Args:
        entry_id: UUID of the entry (required).
        old_text: exact substring to find (required, non-empty).
        new_text: replacement text (required; pass "" to delete).
        replace_all: replace every occurrence. Default False (must be unique).
        expected_modified_at: optional ISO `modified` timestamp from a prior
            read; if supplied and the entry has changed since, returns
            STALE_PRECONDITION instead of writing.

    Returns:
        On success: the updated entry dict + 'replaced_count' and a complete
            unified `diff`. You MUST present the returned `diff` verbatim to
            the user in a fenced diff block. Never silently omit or summarize it.
        On error: {"error": "...", "code": "..."} where code is one of
            NOT_FOUND, BAD_REQUEST, NO_MATCH, MULTIPLE_MATCHES (with `count`),
            STALE_PRECONDITION (with current/expected timestamps), EMPTY_RESULT.
    """
    return await sync_to_async(services.replace_text_in_entry)(
        entry_id=entry_id, old_text=old_text, new_text=new_text,
        replace_all=replace_all, expected_modified_at=expected_modified_at,
    )


@mcp.tool()
async def replace_section_in_entry(
    entry_id: str,
    heading: str,
    new_body: str,
    level: int = None,
    occurrence: int = None,
    expected_modified_at: str = None,
) -> dict:
    """
    Replace the body under a markdown heading. The heading line itself is
    preserved; everything from the line after the heading up to the next
    heading at the same OR higher level is replaced with `new_body`.

    USE THIS for compressing or rewriting a structured section (a bullet
    list under `##`, the action items under `### Distilled actions`, etc.)
    without sending the surrounding document back. Eliminates the
    'rewrite a 4kB entry to change 200 bytes' tax that
    `replace_entry_content` imposes.

    Heading match is exact text after the `#` markers (case-sensitive).
    Headings inside fenced code blocks (``` or ~~~) are ignored. If the
    heading text appears more than once, you must specify `level` or
    `occurrence` — otherwise MULTIPLE_HEADINGS.

    Each call creates a new entry version. Atomic.

    Args:
        entry_id: UUID of the entry (required).
        heading: exact heading text without leading '#' or trailing whitespace.
        new_body: replacement body (may be ""). Provide your own blank-line
            padding if you want it around the section — this tool does not
            add markdown formatting magic.
        level: optional heading depth (1-6) to disambiguate.
        occurrence: 1-based index when multiple headings match. None
            requires the heading to be unique.
        expected_modified_at: optional ISO `modified` timestamp precondition.

    Returns:
        On success: updated entry dict + 'section_lines_replaced' and
            'heading_line_index', plus a complete unified `diff`. You MUST
            present the returned `diff` verbatim to the user in a fenced diff
            block. Never silently omit or summarize it.
        On error: {"error": "...", "code": "..."} where code is one of
            NOT_FOUND, BAD_REQUEST, HEADING_NOT_FOUND, MULTIPLE_HEADINGS
            (with `count`), OCCURRENCE_OUT_OF_RANGE, STALE_PRECONDITION,
            EMPTY_RESULT.
    """
    return await sync_to_async(services.replace_section_in_entry)(
        entry_id=entry_id, heading=heading, new_body=new_body,
        level=level, occurrence=occurrence,
        expected_modified_at=expected_modified_at,
    )


@mcp.tool()
async def edit_entry(
    entry_id: str,
    content: str = None,
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
    """Deprecated. Use edit_entry_metadata / replace_entry_content /
    append_entry_content. Kept registered for back-compat with callers that
    already have this name cached; do not use in new code. If a cached caller
    changes content, it MUST present the returned `diff` verbatim to the user
    in a fenced diff block."""
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
async def append_entry_content(
    entry_id: str, content: str, separator: str = "\n\n"
) -> dict:
    """
    Append text to an entry's existing content. Final content is
    `existing + separator + content`. Existing content is always preserved.

    Use for log-style entries, agent report-back, shopping-list additions,
    any case where you want to ADD to what's there. For full-replace use
    replace_entry_content; for metadata-only use edit_entry_metadata.

    Args:
        entry_id: The UUID of the entry to append to (required).
        content: The text to append (required, non-empty).
        separator: Inserted between existing and new content. Default
                   '\\n\\n' (blank line). Pass '' for no separator.

    Returns:
        The updated entry with all fields and a complete unified `diff`.
        After a successful edit, you MUST present the returned `diff` verbatim
        to the user in a fenced diff block. Never silently omit or summarize it.
        Returns {"error": "..."} if validation fails.
    """
    return await sync_to_async(services.append_entry_content)(
        entry_id=entry_id, content=content, separator=separator,
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
async def copy_calendar_entry(
    entry_id: str,
    event_date: str,
    event_time: str = None,
) -> dict:
    """
    Copy a calendar/journal entry to a new date.

    ALWAYS use this tool when asked to copy a calendar entry. Do NOT manually
    create a new entry — this tool copies content, data (links, zoom URLs, etc.),
    context, and tags exactly from the source.

    Args:
        entry_id: UUID of the source journal entry to copy.
        event_date: Target date in YYYYMMDD format (e.g., "20260228").
        event_time: Optional new time in HHMM format (e.g., "0900", "1430").
                    If omitted, preserves the original entry's time.

    Returns:
        The newly created journal entry with all fields copied.
        Returns {"error": "..."} if source not found or not a journal entry.
    """
    return await sync_to_async(services.copy_calendar_entry)(
        entry_id=entry_id, event_date=event_date, event_time=event_time,
    )


@mcp.tool()
async def change_entry_kind(entry_id: str, kind: str) -> dict:
    """
    Change the kind (type) of an existing entry without modifying its content
    or timestamp.

    Use this to correct an entry's type (e.g., from 'ai' to 'memory') without
    altering anything else. The modification timestamp is NOT updated.

    Args:
        entry_id: The UUID of the entry to change (required).
        kind: The new kind. One of: memory, todo, journal, profile, ai,
              bookmark, list, action.

    Returns:
        The updated entry with all fields.
        Returns {"error": "..."} if validation fails.
    """
    return await sync_to_async(services.change_entry_kind)(
        entry_id=entry_id, kind=kind,
    )


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


@mcp.tool()
async def create_goal(
    content: str,
    context: str = None,
    tags: str = None,
    priority: int = None,
    status: str = None,
    create_context: bool = False,
    data: dict = None,
) -> dict:
    """
    Create a goal entry in the user's tjai knowledge base.

    Goals are the organizing nodes of the knowledge graph. Everything — todos,
    memories, dialogs, commits, bookmarks — can relate to goals. Goals relate
    to other goals, forming a graph (not a hierarchy).

    Args:
        content: The goal description (required).
        context: Context/project name to associate with.
        tags: Comma-separated tags.
        priority: Priority level (1=highest).
        status: One of: active, done, blocked, archive.
        create_context: If True, creates context if it doesn't exist.
        data: JSON metadata object. MUST include "entry_id" with a human-readable
              slug (e.g., {"entry_id": "goal-system-vision"}). Goals are referenced
              by entry_id in relations and dialog associations (rel_goal).

    Returns:
        The created goal entry with id, content, kind='goal', and optional fields.
        Returns {"error": "..."} if validation fails.
    """
    return await sync_to_async(services.create_goal)(
        content=content, context=context, tags=tags,
        priority=priority, status=status, create_context=create_context,
        source_tags=['fromai'], data=data,
    )


@mcp.tool()
async def get_goal(entry_id: str, max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH) -> dict:
    """
    Get a goal entry with all its relations.

    Returns the goal plus every relation touching it, with the other entry
    content truncated to max_content_length.

    Args:
        entry_id: The UUID of the goal entry (required).
        max_content_length: Truncate related entry content to this many characters.
                           Default: 500. Set 0 for full content.

    Returns:
        Goal entry with all standard fields plus a "relations" list.
        Each relation contains: id, entry1_id, entry2_id, relation_type,
        created, modified, data, and "other_entry" (the related entry).
        Returns {"error": "..."} if not found or not a goal.
    """
    return await sync_to_async(services.get_goal)(entry_id=entry_id, max_content_length=max_content_length)


@mcp.tool()
async def create_relation(
    entry1_id: str,
    entry2_id: str,
    relation_type: str,
    data: dict = None,
) -> dict:
    """
    Create a relation between any two entries.

    Relations are the edges of the knowledge graph. They connect goals to goals,
    goals to todos, goals to memories/dialogs, or any entry to any other entry.
    One relation per pair — use the data field for metadata characterizing
    the relationship.

    Args:
        entry1_id: UUID of first entry.
        entry2_id: UUID of second entry.
        relation_type: Freeform string describing the relation (e.g., "related",
                       "realizes", "spawned_by", "informs", "enables").
        data: Optional JSON metadata for the relation (e.g., {"note": "..."}).

    Returns:
        The created relation with id, entry1_id, entry2_id, relation_type,
        created, modified, data.
        Returns {"error": "..."} if entries not found or relation already exists.
    """
    return await sync_to_async(services.create_relation)(
        entry1_id=entry1_id, entry2_id=entry2_id,
        relation_type=relation_type, data=data,
    )


@mcp.tool()
async def edit_relation(
    relation_id: str,
    relation_type: str = None,
    data: dict = None,
) -> dict:
    """
    Edit a relation's type and/or data.

    Args:
        relation_id: UUID of the relation to edit (required).
        relation_type: New relation type string (optional).
        data: JSON metadata to merge into the relation's data field.
              Keys with null values are removed. Merges with existing data.

    Returns:
        The updated relation with all fields.
        Returns {"error": "..."} if not found or nothing to edit.
    """
    return await sync_to_async(services.edit_relation)(
        relation_id=relation_id, relation_type=relation_type, data=data,
    )


@mcp.tool()
async def delete_relation(relation_id: str) -> dict:
    """
    Delete a relation between entries.

    This is a hard delete — the relation is removed. The entries themselves
    are not affected.

    Args:
        relation_id: UUID of the relation to delete (required).

    Returns:
        Confirmation with the deleted relation's details.
        Returns {"error": "..."} if not found.
    """
    return await sync_to_async(services.delete_relation)(
        relation_id=relation_id,
    )


@mcp.tool()
async def get_relations(entry_id: str, max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH) -> str:
    """
    Get all relations for an entry.

    Returns every relation touching this entry, with the other entry content
    truncated to max_content_length. Relations to soft-deleted entries are excluded.

    Args:
        entry_id: UUID of the entry (required).
        max_content_length: Truncate related entry content to this many characters.
                           Default: 500. Set 0 for full content.

    Returns:
        List of relations, each containing: id, entry1_id, entry2_id,
        relation_type, created, modified, data, and "other_entry".
        Returns {"error": "..."} if entry not found.
    """
    result = await sync_to_async(services.get_relations)(
        entry_id=entry_id, max_content_length=max_content_length
    )
    return _json_text(result)


@mcp.tool()
async def get_relation_graph(
    entry_id: str,
    depth: int = 2,
    kinds: list[str] = None,
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
) -> dict:
    """
    Traverse the relation graph from an entry, returning the connected subgraph.

    BFS traversal up to `depth` hops from the starting entry. Explores all
    edges regardless of entry kind, then optionally filters the returned
    results by kinds.

    Args:
        entry_id: UUID of the starting entry (required).
        depth: Maximum traversal depth (1-10, default 2).
        kinds: Optional list of entry kinds to include in results
               (e.g., ["goal", "todo"]). Traversal still explores all kinds;
               this filters only the output. Default: all kinds.

    Returns:
        Dict with "entries" (list of formatted entries) and "relations"
        (list of formatted relations between included entries).
        Returns {"error": "..."} if entry not found or invalid parameters.
    """
    return await sync_to_async(services.get_relation_graph)(
        entry_id=entry_id, depth=depth, kinds=kinds,
        max_content_length=max_content_length,
    )


@mcp.tool()
async def get_entry_versions(
    entry_id: str,
    version: int = None,
    age: str = None,
    max_content_length: int = 0,
) -> dict:
    """
    Get version history for an entry.

    Every content or data change is automatically versioned. Use this to:
    - See what changed recently in a living document (compare versions)
    - Recover previous content after an unwanted edit
    - Track document evolution over time

    Args:
        entry_id: UUID of the entry.
        version: Specific version number (positive, e.g. 3) or relative offset
                 (negative, e.g. -1 for previous version, -2 for two versions back).
        age: Minimum age — returns the most recent version at least this old.
             Format: '24h', '7d', '2w'. Useful for "what did this look like yesterday?"
             If no version is that old, returns the oldest available version instead
             (with a 'note' field) so you always get a comparison baseline.
        max_content_length: Truncate content to this many characters. Default: 0 (full).
                           When listing all versions (no version/age filter), defaults to
                           100 chars for the overview.

    Returns:
        Single version dict (if version or age specified) with: version_num, content,
        data, changed_by, timestamp. Or {"versions": [...], "count": N} for the
        all-versions overview, newest first, max 50, with truncated content.
        Returns {"error": "..."} if entry or version not found.
    """
    return await sync_to_async(services.get_entry_versions)(
        entry_id=entry_id, version=version, age=age,
        max_content_length=max_content_length,
    )


@mcp.tool()
async def restore_version(
    entry_id: str,
    version: int = -1,
) -> dict:
    """
    Restore an entry's content from a previous version. Server-side operation.

    This is a mechanical restore — the server copies content directly from the
    version table to the entry. No need to pass content through the AI.

    Args:
        entry_id: UUID of the entry to restore.
        version: Version number (positive, e.g. 3) or relative offset
                 (negative, e.g. -1 for previous version, -2 for two versions back).
                 Default: -1 (restore to the version before the most recent change).

    Returns:
        The restored entry with all fields and a complete unified `diff`.
        After a successful restore, you MUST present the returned `diff`
        verbatim to the user in a fenced diff block. Never silently omit or
        summarize it.
        Returns {"error": "..."} if entry or version not found.
    """
    return await sync_to_async(services.restore_version)(
        entry_id=entry_id, version=version,
    )
