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
    - tj ai - list universal + current context AI guidelines (if in a context)
    - tj ai =context - list universal + specified context AI guidelines
    - tj ai :tag - list universal + tag-specific AI guidelines
    """
    from tj.state import get_state
    if not hasattr(args, 'input') or not args.input:
        # No args: list universal + current context AI guidelines
        state = get_state()
        current_context = state.get("current_context")
        query_ai_guidelines(context=current_context, tag=None)
        return

    first_arg = args.input[0]

    # Query by context (only if there's no content after the context arg)
    if first_arg.startswith('=') and len(args.input) == 1:
        context_name = first_arg[1:]
        if not context_name:
            print("Error: Empty context name.", file=sys.stderr)
            return
        query_ai_guidelines(context=context_name, tag=None)
        return

    # Query by tag (only if there's no content after the tag arg)
    if first_arg.startswith(':') and len(args.input) == 1:
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

        # Get all tags once (not per entry)
        tags_by_entry = repository.get_tags_by_entry()

        # Separate universal from specific
        universal = []
        specific = []

        for entry in active_ai:
            entry_tags = tags_by_entry.get(entry.id, [])

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

        from tj.colors import colorize_content

        # Always show universal guidelines
        if universal:
            print("Universal:")
            universal.sort(key=lambda e: e.timestamp_created)
            for i, entry in enumerate(universal, 1):
                print(f"{i}. {colorize_content(entry.content)}")
            print()

        # Show specific guidelines if context/tag was requested
        if context or tag:
            if specific:
                if context:
                    # Get context details to show description
                    context_obj = repository.get_context(context)
                    if context_obj and context_obj.description:
                        print(f"For context '{context}': {context_obj.description}")
                    else:
                        print(f"For context '{context}':")
                else:
                    print(f"For tag ':{tag}':")
                specific.sort(key=lambda e: e.timestamp_created)
                for i, entry in enumerate(specific, 1):
                    print(f"{i}. {colorize_content(entry.content)}")
            else:
                scope = f"context '{context}'" if context else f"tag ':{tag}'"
                if not universal:
                    print(f"No AI guidelines found for {scope}.")

        elif not universal:
            print("No universal AI guidelines found.")

    except Exception as e:
        print(f"Error querying AI guidelines: {e}", file=sys.stderr)
