import sys
from typing import Optional
from tj.state import get_state, save_state, display_context


def handle_context(args, num_identifier: Optional[int] = None) -> None:
    """Handles setting or clearing the context."""
    from tj.repository_factory import RepositoryFactory
    from tj.repository import Context
    from datetime import datetime, timezone

    state = get_state()

    if args.name:
        repository = RepositoryFactory.get_repository()

        # Check if context already exists
        existing_context = repository.get_context(args.name)

        # Extract title and description from args
        title = getattr(args, 'title', None)
        description = " ".join(args.description) if args.description else None

        now = datetime.now(timezone.utc).timestamp()

        if not existing_context:
            # Context doesn't exist - confirm creation
            if not confirm_action(f"Context '{args.name}' does not exist. Create it?"):
                print("Cancelled.")
                return

            # Create new context
            new_context = Context(
                name=args.name,
                title=title,
                description=description,
                timestamp_created=now,
                timestamp_modified=now
            )
            repository.create_context(new_context)

            parts = [f"'{args.name}'"]
            if title:
                parts.append(f"title='{title}'")
            if description:
                parts.append(f"description='{description}'")
            print(f"Context {' with '.join(parts)} created.")

        # Context exists - update if title or description provided
        elif title or description:
            updates = {}
            if title:
                updates['title'] = title
            if description:
                updates['description'] = description
            updates['timestamp_modified'] = now

            repository.update_context(args.name, **updates)

            parts = []
            if title:
                parts.append(f"title='{title}'")
            if description:
                parts.append(f"description='{description}'")
            print(f"Context '{args.name}' updated with {', '.join(parts)}.")
        else:
            # Just switching to existing context
            print(f"Context set to: {args.name}")

        # Set as current context
        state["current_context"] = args.name
        save_state(state)
        
    # else branch removed - tj = handled in cli.py
