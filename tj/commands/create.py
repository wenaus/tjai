import json
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

from tj.database import DatabaseError
from tj.repository import Entry
from tj.repository_factory import RepositoryFactory
from tj.state import get_state, save_state, display_context

def handle_creation(args, entry_type_override: Optional[str] = None, num_identifier: Optional[int] = None) -> None:
    """Handles the creation of a new entry."""
    display_context()
    
    if not hasattr(args, 'input') or not args.input:
        print("Error: No content provided for entry.", file=sys.stderr)
        return
    
    try:
        # Keep the full content including tags, but extract tags separately
        content = " ".join(args.input).strip()
        if not content:
            print("Error: Entry content cannot be empty.", file=sys.stderr)
            return
        
        # Extract tags for separate storage, but leave them in the content
        tags = set()
        for part in args.input:
            if part.startswith(':'):
                tag_name = part[1:]
                if tag_name:  # Ensure tag is not empty
                    tags.add(tag_name)
            
        entry_type = entry_type_override
        event_date = None

        # Smart type detection and URL extraction
        extracted_url = None
        if not entry_type:
            content_parts = content.split()
            if content_parts:
                # Date detection (YYYYMMDD format)
                if re.match(r'^\d{8}$', content_parts[0]):
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
                    
                else:
                    entry_type = 'memory'
            else:
                entry_type = 'memory'

        repository = RepositoryFactory.get_repository()
        
        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).timestamp()
        state = get_state()
        
        # Prepare data JSON
        data = {}
        if event_date:
            data['event_date'] = event_date
        if extracted_url:
            data['url'] = extracted_url
        
        # Create entry object
        entry = Entry(
            id=entry_id,
            content=content,
            kind=entry_type,
            timestamp_created=now,
            timestamp_modified=now,
            context=state.get("current_context"),
            is_dirty=True,
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
        
        print(f"Created {entry_type}: {content[:50]}{'...' if len(content) > 50 else ''}")
        
    except DatabaseError as e:
        print(f"Database error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Error creating entry: {e}", file=sys.stderr)
