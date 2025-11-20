"""Editor integration for tj entry creation and editing."""

import os
import subprocess
import tempfile
from typing import Optional


def get_editor_command() -> str:
    """Get editor command from $EDITOR environment variable or fallback to vi."""
    editor = os.environ.get('EDITOR')
    if editor:
        return editor
    # Fallback to vi (universal on Unix systems)
    return 'vi'


def open_editor(initial_content: str = "") -> Optional[str]:
    """Open editor with content, return modified content or None if cancelled.

    Args:
        initial_content: Initial text to populate the editor with

    Returns:
        Modified content as string, or None if cancelled/unchanged/empty
    """
    # Create temp file with .md suffix for syntax highlighting
    fd, temp_path = tempfile.mkstemp(suffix='.md', text=True)

    try:
        # Write initial content and close file descriptor
        with os.fdopen(fd, 'w') as f:
            f.write(initial_content)

        # Get editor command
        editor = get_editor_command()

        # Build command with wait flag for known editors
        editor_cmd = [editor, temp_path]

        # Add --wait flag for editors that need it
        editor_name = os.path.basename(editor).lower()
        if editor_name in ['bbedit', 'mate', 'subl', 'code']:
            # BBEdit, TextMate, Sublime, VS Code need --wait
            editor_cmd = [editor, '--wait', temp_path]
        elif editor_name == 'nano':
            # nano blocks by default
            editor_cmd = [editor, temp_path]

        # Launch editor (blocks until user closes)
        try:
            result = subprocess.call(editor_cmd)
        except FileNotFoundError:
            print(f"Error: Editor '{editor}' not found")
            return None
        except Exception as e:
            print(f"Error launching editor: {e}")
            return None

        if result != 0:
            print(f"Editor exited with error code {result}")
            return None

        # Read modified content
        with open(temp_path, 'r') as f:
            content = f.read()

        # Strip trailing whitespace
        content = content.rstrip()

        if not content:
            return None

        # Check if unchanged
        if content == initial_content.rstrip():
            return None

        return content

    except KeyboardInterrupt:
        print("\nEditor cancelled by user.")
        return None
    finally:
        # Clean up temp file
        try:
            os.unlink(temp_path)
        except:
            pass


def handle_editor_create(entry_type: Optional[str] = None, extra_args: Optional[list] = None) -> None:
    """Handle creating entry via editor.

    Args:
        entry_type: Optional entry type (ai, todo, profile, bookmark, calendar)
        extra_args: Optional extra arguments like =context
    """
    content = open_editor()

    if content is None:
        return

    # Parse first line for =context, extract it as separate arg
    lines = content.split('\n', 1)
    first_line = lines[0]
    rest = lines[1] if len(lines) > 1 else None

    # Split first line to separate =context from content
    first_parts = first_line.split()
    metadata = []  # =context items
    content_parts = []  # actual content words

    for part in first_parts:
        if part.startswith('='):
            metadata.append(part)
        else:
            content_parts.append(part)

    # Add extra_args (like =context from command line)
    if extra_args:
        metadata.extend(extra_args)

    # Reconstruct full content (without =context on first line)
    if content_parts:
        first_content = ' '.join(content_parts)
        if rest:
            full_content = first_content + '\n' + rest
        else:
            full_content = first_content
    elif rest:
        full_content = rest
    else:
        print("Error: Entry content cannot be empty.")
        return

    # Build input list: metadata first, then content as single item (preserves newlines)
    input_list = metadata + [full_content]

    # Create args object
    class Args:
        def __init__(self):
            self.input = input_list
            self.timestamp_override = None

    args = Args()

    # Call existing creation handler
    from tj.commands.create import handle_creation
    handle_creation(args, entry_type_override=entry_type)


