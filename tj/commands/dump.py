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


def format_entry_command(entry: Entry, tags: List[str]) -> str:
    """Format a single entry as a tj command.

    Returns the command string to recreate this entry.
    """
    # Check if content has newlines - use heredoc if so
    has_newlines = '\n' in entry.content

    # Build command parts
    cmd_parts = []

    # 1. Base command based on kind
    if entry.kind == 'todo':
        cmd_parts.append('tj d')
    elif entry.kind == 'profile':
        cmd_parts.append('tj p')
    elif entry.kind == 'ai':
        cmd_parts.append('tj ai')
    else:
        # Default command for memory, bookmark, calendar, etc.
        cmd_parts.append('tj')

    # 2. Add context if present (inline context switching)
    if entry.context:
        cmd_parts.append(f'={entry.context}')

    # 3. Add timestamp
    cmd_parts.append(format_timestamp(entry.timestamp_created))

    # 4. Build the command
    if has_newlines:
        # Use heredoc format
        cmd_line = ' '.join(cmd_parts) + ' <<!'
        content_block = escape_content_for_heredoc(entry.content)
        # Add tags to the last line of content if present
        if tags:
            content_block += ' ' + ' '.join(f':{tag}' for tag in tags)
        return f"{cmd_line}\n{content_block}\n!"
    else:
        # Single line format
        # Content with tags inline
        content = entry.content
        if tags:
            content += ' ' + ' '.join(f':{tag}' for tag in tags)
        cmd_parts.append(content)
        return ' '.join(cmd_parts)


def handle_dump(args) -> None:
    """Handle the dump command - output database as executable tj commands."""
    try:
        repository = RepositoryFactory.get_repository()

        # 1. Dump context definitions first
        contexts = repository.get_all_contexts()
        if contexts:
            print("# Context definitions")
            for context in sorted(contexts, key=lambda c: c.timestamp_created):
                cmd_parts = [f'tj ={context.name}']
                if context.title:
                    cmd_parts.append(f'-t {context.title}')
                if context.description:
                    cmd_parts.append(f'-d {context.description}')
                print(' '.join(cmd_parts))
            print()  # Blank line after contexts

        # 2. Dump entries in ROWID order (actual insertion order)
        # Query raw from database to get ROWID ordering
        from tj.database import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, parent_id, content, kind, timestamp_created,
                   timestamp_modified, context, is_dirty, data
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
                    data=json.loads(row['data']) if row['data'] else None
                )

                # Get tags for this entry
                tags = repository.get_tags(entry.id)

                # Format and output command
                cmd = format_entry_command(entry, tags)
                print(cmd)

        # Stats
        print(f"\n# Dumped {len(contexts)} contexts, {len(rows)} entries", file=sys.stderr)

    except Exception as e:
        print(f"Dump error: {e}", file=sys.stderr)
