import sys
from typing import Optional
from tj.state import get_state, save_state

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
        
        if args.description:
            # Creating/updating context with description
            description = " ".join(args.description)
            now = datetime.now(timezone.utc).timestamp()
            
            if existing_context:
                # Context exists - ask for confirmation to update description
                print(f"Context '{args.name}' exists with description: '{existing_context.description}'")
                response = input(f"Update description to '{description}' [y/N]: ").strip().lower()
                
                if response in ['y', 'yes']:
                    # Update existing context
                    repository.update_context(args.name, description=description, timestamp_modified=now)
                    print(f"Context '{args.name}' description updated.")
                else:
                    print("Context update cancelled.")
                    return
            else:
                # Create new context
                new_context = Context(
                    name=args.name,
                    description=description,
                    timestamp_created=now,
                    timestamp_modified=now
                )
                repository.create_context(new_context)
                print(f"Context '{args.name}' created with description: '{description}'")
        else:
            # Just setting context without description
            if not existing_context:
                # Create context with empty description
                now = datetime.now(timezone.utc).timestamp()
                new_context = Context(
                    name=args.name,
                    description="",
                    timestamp_created=now,
                    timestamp_modified=now
                )
                repository.create_context(new_context)
                print(f"Context '{args.name}' created.")
            else:
                print(f"Context set to: {args.name}")
        
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
