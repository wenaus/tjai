"""AI command for creating and querying AI behavioral guidelines."""

import sys
from typing import Optional

from tj.repository_factory import RepositoryFactory
from tj.commands.create import handle_creation


def handle_ai_command(args, num_identifier: Optional[int] = None) -> None:
    """Handle AI guideline creation and queries.

    Creation:
    - tj ai <content> - create universal AI guideline
    - tj ai =context <content> - create context-specific AI guideline
    - tj ai :<tag> <content> - create tagged AI guideline

    Query (for AI consumption):
    - tj ai - list universal AI guidelines only
    - tj ai =context - list universal + context-specific AI guidelines
    - tj ai :tag - list universal + tag-specific AI guidelines
    """
    from tj.state import display_context
    display_context()
    if not hasattr(args, 'input') or not args.input:
        # No args: list universal AI guidelines only
        query_ai_guidelines(context=None, tag=None)
        return

    first_arg = args.input[0]

    # Query by context
    if first_arg.startswith('='):
        context_name = first_arg[1:]
        if not context_name:
            print("Error: Empty context name.", file=sys.stderr)
            return
        query_ai_guidelines(context=context_name, tag=None)
        return

    # Query by tag
    if first_arg.startswith(':'):
        tag_name = first_arg[1:]
        if not tag_name:
            print("Error: Empty tag name.", file=sys.stderr)
            return
        query_ai_guidelines(context=None, tag=tag_name)
        return

    # Creation: delegate to handle_creation with ai entry type
    # Extract at= timestamp if present
    from tj.cli import parse_at_timestamp
    if hasattr(args, 'input') and args.input:
        timestamp_override, filtered_input = parse_at_timestamp(args.input)
        args.input = filtered_input
        args.timestamp_override = timestamp_override
    handle_creation(args, entry_type_override='ai')


def query_ai_guidelines(context: Optional[str], tag: Optional[str]) -> None:
    """Query and display AI guidelines for AI consumption.

    Shows universal guidelines always, plus context/tag-specific if requested.
    """
    try:
        repository = RepositoryFactory.get_repository()

        # Get all AI entries
        all_ai_entries = repository.query_entries(kind='ai')
        active_ai = [e for e in all_ai_entries if not getattr(e, 'deleted_at', None)]

        # Separate universal from specific
        universal = []
        specific = []

        for entry in active_ai:
            entry_tags = repository.get_tags(entry.id)

            # Check if universal (no context, no tags)
            is_universal = not entry.context and not entry_tags

            if is_universal:
                universal.append(entry)
            elif context and entry.context == context:
                specific.append(entry)
            elif tag and tag in entry_tags:
                specific.append(entry)

        # Print header
        print("AI Guidelines - Follow these instructions:")
        print()

        # Always show universal guidelines
        if universal:
            print("Universal:")
            universal.sort(key=lambda e: e.timestamp_created)
            for i, entry in enumerate(universal, 1):
                print(f"{i}. {entry.content}")
            print()

        # Show specific guidelines if context/tag was requested
        if context or tag:
            if specific:
                scope = f"context '{context}'" if context else f"tag ':{tag}'"
                print(f"For {scope}:")
                specific.sort(key=lambda e: e.timestamp_created)
                for i, entry in enumerate(specific, 1):
                    print(f"{i}. {entry.content}")
            else:
                scope = f"context '{context}'" if context else f"tag ':{tag}'"
                if not universal:
                    print(f"No AI guidelines found for {scope}.")

        elif not universal:
            print("No universal AI guidelines found.")

    except Exception as e:
        print(f"Error querying AI guidelines: {e}", file=sys.stderr)
