"""Dump command - outputs database as executable tj commands."""

import sys
from datetime import datetime
from typing import List

from tj.repository_factory import RepositoryFactory
from tj.repository import Entry, Context


def format_timestamp(ts: float) -> str:
    """Format timestamp as at=YYYYMMDD/HH:MM."""
    dt = datetime.fromtimestamp(ts)
    return f"at={dt.strftime('%Y%m%d/%H:%M')}"


def escape_content_for_heredoc(content: str) -> str:
    """Prepare content for heredoc - no escaping needed, literal text."""
    return content


def format_entry_command(entry: Entry, tags: List[str], db_path: str = None) -> str:
    """Format a single entry as a tj command.

    Returns the command string to recreate this entry.
    """
    # Check if content has newlines - use heredoc if so
    has_newlines = '\n' in entry.content

    # Build command parts
    cmd_parts = []

    # 0. Add db path if provided
    if db_path:
        cmd_parts.append(f'tj --db={db_path}')
    else:
        cmd_parts.append('tj')

    # 1. Add kind-specific command
    if entry.kind == 'todo':
        cmd_parts[0] += ' d'
    elif entry.kind == 'profile':
        cmd_parts[0] += ' p'
    elif entry.kind == 'ai':
        cmd_parts[0] += ' ai'

    # 2. Add context if present (inline context switching)
    if entry.context:
        cmd_parts.append(f'={entry.context}')

    # 3. Add timestamp
    cmd_parts.append(format_timestamp(entry.timestamp_created))

    # 4. Add name if present
    if entry.name:
        cmd_parts.append(f'@{entry.name}')

    # 5. Add priority if present
    if entry.priority is not None:
        cmd_parts.append(f'p={entry.priority}')

    # 6. Add status if present
    if entry.status:
        cmd_parts.append(f's={entry.status}')

    # 7. Add links if present
    if entry.data and 'links' in entry.data:
        links = entry.data['links']
        for link in links:
            title = link.get('title', 'Link')
            url = link.get('url', '')
            # Special case: title "Link" uses // notation
            if title == 'Link':
                cmd_parts.append(f'//{url}')
            else:
                cmd_parts.append(f'[{title}]({url})')

    # 8. Build the command
    if has_newlines:
        # Use command substitution with cat heredoc (works when sourced)
        cmd_line = ' '.join(cmd_parts)
        content_block = escape_content_for_heredoc(entry.content)
        # Tags are already embedded in content, don't add again
        return f"{cmd_line} \"$(cat <<'END'\n{content_block}\nEND\n)\""
    else:
        # Single line format
        # Tags are already embedded in content, don't add again
        cmd_parts.append(entry.content)
        return ' '.join(cmd_parts)


def handle_dump(args) -> None:
    """Handle the dump command - output database as executable tj commands."""
    try:
        repository = RepositoryFactory.get_repository()

        # Get current db path to include in dump
        from tj.database import get_configured_db_path, _DB_OVERRIDE
        db_path = _DB_OVERRIDE.name if _DB_OVERRIDE else None

        # 1. Dump context definitions first
        contexts = repository.get_all_contexts()
        if contexts:
            print("# Context definitions")
            for context in sorted(contexts, key=lambda c: c.timestamp_created):
                cmd_parts = []
                if db_path:
                    cmd_parts.append(f'tj --db={db_path}')
                else:
                    cmd_parts.append('tj')
                cmd_parts.append(f'={context.name}')
                if context.title:
                    cmd_parts.append(f'-t "{context.title}"')
                if context.description:
                    cmd_parts.append(f'-d "{context.description}"')
                print(' '.join(cmd_parts))
            print()  # Blank line after contexts

        # 2. Dump entries in ROWID order (actual insertion order)
        # Query raw from database to get ROWID ordering
        from tj.database import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, parent_id, content, kind, timestamp_created,
                   timestamp_modified, context, is_dirty, name, priority, status, data
            FROM entries
            WHERE deleted_at IS NULL
            ORDER BY ROWID
        """)

        rows = cursor.fetchall()
        conn.close()

        if rows:
            print("# Entries in insertion order")
            import json
            for row in rows:
                # Reconstruct Entry object
                entry = Entry(
                    id=row['id'],
                    parent_id=row['parent_id'],
                    content=row['content'],
                    kind=row['kind'],
                    timestamp_created=row['timestamp_created'],
                    timestamp_modified=row['timestamp_modified'],
                    context=row['context'],
                    is_dirty=bool(row['is_dirty']),
                    name=row['name'],
                    priority=row['priority'],
                    status=row['status'],
                    data=json.loads(row['data']) if row['data'] else None
                )

                # Get tags for this entry
                tags = repository.get_tags(entry.id)

                # Format and output command
                cmd = format_entry_command(entry, tags, db_path)
                print(cmd)

        # Blank line at end
        print()

        # Stats
        print(f"# Dumped {len(contexts)} contexts, {len(rows)} entries", file=sys.stderr)

    except Exception as e:
        print(f"Dump error: {e}", file=sys.stderr)
