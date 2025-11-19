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

        if existing_context:
            # Context exists - update if title or description provided
            if title or description:
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
        else:
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

        # Set as current context
        state["current_context"] = args.name
        save_state(state)
        
    else:
        # Show current context and ask for confirmation to clear
        current_context = state.get("current_context")
        
        if not current_context:
            print("Current context: none")
            return
        
        # Ask for confirmation before clearing (defaults to N)
        response = input(f"Clear current context '{current_context}' [y/N]: ").strip().lower()
        if response in ['y', 'yes']:
            state["current_context"] = None
            print("Context cleared.")
            save_state(state)
        else:
            print("Context clear cancelled.")
