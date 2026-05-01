"""tjai tools for Claude - Telegram bot adapter."""

import os
import sys

# Django setup must happen before model imports
sys.path.insert(0, str(__file__).rsplit('/', 2)[0])
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tjai_project.settings')

import django
django.setup()

from tjai_app import services


# Tool definitions for Claude API (bot LLM schema)
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
                "limit": {"type": "integer", "description": "Page size. Default: 50. Hard maximum: 500."},
                "offset": {"type": "integer", "description": "Zero-based result offset for pagination. Default: 0. If a page returns exactly limit results, more may exist; at your discretion, fetch the next page with offset + limit."},
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
                "limit": {"type": "integer", "description": "Page size. Default: 50. Hard maximum: 500."},
                "offset": {"type": "integer", "description": "Zero-based result offset for pagination. Default: 0. If a page returns exactly limit results, more may exist; at your discretion, fetch the next page with offset + limit."},
            },
        },
    },
    {
        "name": "search_entries",
        "description": "Search or list entries with optional full-text query and structured filters.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional search terms. Omit for structured listing/filtering by kind, context, or date."},
                "kind": {"type": "string", "description": "Filter by type: memory, todo, journal, profile, ai, bookmark, list, action, goal."},
                "context": {"type": "string", "description": "Filter to this context."},
                "start_date": {"type": "string", "description": "Start of date range. Supports YYYYMMDD, 'yesterday', '3d', 'monday', etc. Default: 7 days ago."},
                "end_date": {"type": "string", "description": "End of date range. Default: now."},
                "limit": {"type": "integer", "description": "Page size. Default: 50. Hard maximum: 500."},
                "offset": {"type": "integer", "description": "Zero-based result offset for pagination. Default: 0. If a page returns exactly limit results, more may exist; at your discretion, fetch the next page with offset + limit."},
                "order_by": {"type": "string", "description": "Sort order: time (default), rank (requires query), or size."},
            },
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
        "name": "replace_entry_content",
        "description": "Replace an entry's content with new text (destructive full rewrite). For additive edits use append_entry_content; for metadata-only use edit_entry_metadata.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "The UUID of the entry."},
                "content": {"type": "string", "description": "New content (replaces existing)."},
            },
            "required": ["entry_id", "content"],
        },
    },
    {
        "name": "append_entry_content",
        "description": "Append text to an entry's existing content. Existing content is preserved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "The UUID of the entry."},
                "content": {"type": "string", "description": "Text to append."},
                "separator": {"type": "string", "description": "Inserted between existing and new. Default blank line."},
            },
            "required": ["entry_id", "content"],
        },
    },
    {
        "name": "edit_entry_metadata",
        "description": "Edit metadata fields (status, priority) without changing content. For content changes use replace_entry_content or append_entry_content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "The UUID of the entry."},
                "status": {"type": "string", "description": "New status: active, done, blocked, archive."},
                "priority": {"type": "integer", "description": "New priority (1=highest)."},
            },
            "required": ["entry_id"],
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


# Tool implementations - thin wrappers around services

def get_calendar(start_date=None, end_date=None, context=None, days=None):
    return services.get_calendar(
        start_date=start_date, end_date=end_date, context=context, days=days,
    )


def get_profile():
    return services.get_profile()


def get_ai_guidance(context=None):
    return services.get_ai_guidance(context=context)


def list_contexts():
    return services.list_contexts()


def get_todos(context=None, status=None, include_done=False):
    return services.get_todos(context=context, status=status, include_done=include_done)


def get_memories(context=None, start_date=None, end_date=None, limit=50, offset=0):
    if start_date is None:
        start_date = '7d'
    return services.get_memories(
        context=context, limit=limit, offset=offset,
        start_date=start_date, end_date=end_date,
    )


def get_bookmarks(context=None, start_date=None, end_date=None, limit=50, offset=0):
    if start_date is None:
        start_date = '7d'
    return services.get_bookmarks(
        context=context, limit=limit, offset=offset,
        start_date=start_date, end_date=end_date,
    )


def search_entries(query=None, kind=None, context=None, start_date=None, end_date=None, limit=50, offset=0, order_by='time'):
    if start_date is None:
        start_date = '7d'
    return services.search_entries(
        query=query, kind=kind, context=context, limit=limit, offset=offset,
        start_date=start_date, end_date=end_date, order_by=order_by,
    )


def create_entry(content, kind="memory", context=None, tags=None,
                 event_date=None, event_time=None, priority=None, status=None):
    return services.create_entry(
        content=content, kind=kind, context=context, tags=tags,
        event_date=event_date, event_time=event_time, priority=priority,
        status=status, source_tags=['fromai', 'fromtg'],
    )


def get_entry(entry_id):
    return services.get_entry(entry_id=entry_id)


# Deprecated back-compat shim; not exposed in SCHEMA. Kept so any cached
# caller still using this name continues to work.
def edit_entry(entry_id, content=None, status=None, priority=None):
    return services.edit_entry(
        entry_id=entry_id, content=content, status=status, priority=priority,
    )


def replace_entry_content(entry_id, content):
    return services.replace_entry_content(entry_id=entry_id, content=content)


def append_entry_content(entry_id, content, separator="\n\n"):
    return services.append_entry_content(
        entry_id=entry_id, content=content, separator=separator,
    )


def edit_entry_metadata(entry_id, status=None, priority=None):
    return services.edit_entry_metadata(
        entry_id=entry_id, status=status, priority=priority,
    )


def delete_entry(entry_id, content):
    return services.delete_entry(entry_id=entry_id, content=content)


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
    "replace_entry_content": replace_entry_content,
    "append_entry_content": append_entry_content,
    "edit_entry_metadata": edit_entry_metadata,
    "edit_entry": edit_entry,  # deprecated back-compat; not in SCHEMA
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
