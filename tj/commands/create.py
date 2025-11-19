import json
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

from tj.database import DatabaseError
from tj.repository import Entry
from tj.repository_factory import RepositoryFactory
from tj.state import get_state, save_state, display_context, set_last_parent, set_last_list
from tj.commands.lists import detect_list_creation

def handle_creation(args, entry_type_override: Optional[str] = None, num_identifier: Optional[int] = None) -> None:
    """Handles the creation of a new entry."""
    display_context()

    if not hasattr(args, 'input') or not args.input:
        print("Error: No content provided for entry.", file=sys.stderr)
        return

    try:
        # Step 1: Extract inline =context (must be per-arg)
        inline_context = None
        filtered_input = []
        for part in args.input:
            if part.startswith('='):
                context_name = part[1:]
                if context_name == '0':
                    # Clear context inline
                    inline_context = None
                    state = get_state()
                    state["current_context"] = None
                    save_state(state)
                elif context_name:
                    # Set inline context
                    inline_context = context_name
                    state = get_state()
                    state["current_context"] = inline_context
                    save_state(state)
                    # Auto-create context if it doesn't exist
                    from tj.repository import Context
                    repository = RepositoryFactory.get_repository()
                    if not repository.get_context(inline_context):
                        now = datetime.now(timezone.utc).timestamp()
                        new_context = Context(
                            name=inline_context,
                            title=None,
                            description=None,
                            timestamp_created=now,
                            timestamp_modified=now
                        )
                        repository.create_context(new_context)
                # Don't add =context to the content
            else:
                filtered_input.append(part)

        # Step 2: Join to get full content string
        content = " ".join(filtered_input).strip()

        # Step 2.1: Append file content if provided via -f flag
        import tj.cli
        if hasattr(tj.cli, 'file_content') and tj.cli.file_content:
            if content:
                content = content + "\n" + tj.cli.file_content
            else:
                content = tj.cli.file_content

        if not content:
            print("Error: Entry content cannot be empty.", file=sys.stderr)
            return

        # Step 2.5: Extract @name, p=, s= from content
        entry_name = None
        entry_priority = None
        entry_status = None

        # Extract @name (must start with letter, then alphanumeric/underscore/dash)
        name_pattern = re.compile(r'@([a-zA-Z][a-zA-Z0-9_-]*)')
        name_match = name_pattern.search(content)
        if name_match:
            entry_name = name_match.group(1)
            content = name_pattern.sub('', content, count=1)  # Remove only first occurrence

        # Extract p=N (priority)
        priority_pattern = re.compile(r'\bp=(\d+)\b')
        priority_match = priority_pattern.search(content)
        if priority_match:
            entry_priority = int(priority_match.group(1))
            content = priority_pattern.sub('', content)

        # Extract s=value (status)
        status_pattern = re.compile(r'\bs=(\w+)\b')
        status_match = status_pattern.search(content)
        if status_match:
            entry_status = status_match.group(1)
            content = status_pattern.sub('', content)

        if not content:
            print("Error: Entry content cannot be empty after metadata extraction.", file=sys.stderr)
            return

        # Step 3: Extract links from content (//url and [title](url))
        links = []

        # Extract //url patterns
        url_pattern = re.compile(r'//(?:https?://[^\s]+)')
        for match in url_pattern.finditer(content):
            url = match.group()[2:]  # Remove //
            links.append({"title": "Link", "url": url})
        # Remove //url from content
        content = url_pattern.sub('', content)

        # Extract [title](url) patterns - keep markdown in content, also store separately
        md_link_pattern = re.compile(r'\[([^\]]+)\]\((https?://[^\)]+)\)')
        for match in md_link_pattern.finditer(content):
            title = match.group(1)
            url = match.group(2)
            links.append({"title": title, "url": url})
        # Keep [title](url) in content - do NOT remove it

        if not content.strip():
            print("Error: Entry content cannot be empty.", file=sys.stderr)
            return

        # Step 4: Extract tags for separate storage, but leave them in the content
        tags = set()
        for word in content.split():
            if word.startswith(':'):
                tag_name = word[1:]
                if tag_name:  # Ensure tag is not empty
                    tags.add(tag_name)
            
        entry_type = entry_type_override
        event_date = None

        # Check for event_date_override from journal command
        if hasattr(args, 'event_date_override') and args.event_date_override is not None:
            event_date = args.event_date_override
            # If entry_type not already set, default to calendar
            if not entry_type:
                entry_type = 'calendar'

        # Smart type detection and URL extraction
        extracted_url = None
        if not entry_type:
            content_parts = content.split()
            if content_parts:
                # Bookmark detection - starts with 'b' followed by markdown link
                if content_parts[0] == 'b' and len(content_parts) > 1 and content_parts[1].startswith('['):
                    entry_type = 'bookmark'
                    # Remove the 'b' prefix from content
                    content = ' '.join(content_parts[1:])
                # Bookmark detection - starts with markdown link
                elif re.match(r'^\[.+\]\(https?://', content):
                    entry_type = 'bookmark'
                # Date detection (YYYYMMDD format)
                elif re.match(r'^\d{8}$', content_parts[0]):
                    try:
                        date_str = content_parts[0]
                        event_dt = datetime.strptime(date_str, "%Y%m%d")
                        event_date = event_dt.timestamp()
                        entry_type = 'calendar'
                        # Keep the original content with date, but store parsed date separately
                    except ValueError:
                        entry_type = 'memory'
                # URL detection - check if first part is a URL
                elif re.match(r'^https?://', content_parts[0]):
                    extracted_url = content_parts[0]
                    entry_type = 'bookmark'

                    # If there's additional text after the URL, reformat as "text url"
                    if len(content_parts) > 1:
                        description = " ".join(content_parts[1:])
                        content = f"{description} {extracted_url}"
                    # If URL only, keep as is

                # List detection - content ends with "list" or "checklist"
                elif detect_list_creation(content):
                    entry_type = 'list'

                else:
                    entry_type = 'memory'
            else:
                entry_type = 'memory'

        repository = RepositoryFactory.get_repository()

        entry_id = str(uuid.uuid4())

        # Use timestamp_override if provided, otherwise use current time
        if hasattr(args, 'timestamp_override') and args.timestamp_override is not None:
            now = args.timestamp_override
        else:
            now = datetime.now(timezone.utc).timestamp()

        state = get_state()
        
        # Prepare data JSON
        data = {}
        if event_date:
            data['event_date'] = event_date
        if extracted_url:
            data['url'] = extracted_url
        if entry_type == 'list':
            data['items'] = []
        if links:
            data['links'] = links
        
        # Create entry object
        entry = Entry(
            id=entry_id,
            content=content,
            kind=entry_type,
            timestamp_created=now,
            timestamp_modified=now,
            context=state.get("current_context"),
            is_dirty=True,
            name=entry_name,
            priority=entry_priority,
            status=entry_status,
            data=data if data else None
        )
        
        # Save entry
        repository.create_entry(entry)
        
        # Add tags
        for tag in tags:
            repository.add_tag(entry_id, tag)

        # Update last entry ID for 'a' command
        state["last_entry_id"] = entry_id
        save_state(state)

        # Set as last parent for sub-items (if not a sub-item itself)
        if not entry.parent_id:
            set_last_parent(entry_id)

        # If it's a list, also track as last list
        if entry_type == 'list':
            set_last_list(entry_id)

        print(f"Created {entry_type}: {content[:50]}{'...' if len(content) > 50 else ''}")
        
    except DatabaseError as e:
        print(f"Database error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Error creating entry: {e}", file=sys.stderr)
