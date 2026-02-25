"""Editor integration for tj entry creation and editing."""

import os
import subprocess
import tempfile
import traceback
from typing import Optional


def get_editor_command() -> str:
    """Get editor command from $EDITOR environment variable or fallback to vi."""
    editor = os.environ.get('EDITOR')
    if editor:
        return editor
    # Fallback to vi (universal on Unix systems)
    return 'vi'


def open_editor(initial_content: str = "", entry_type: Optional[str] = None, filename_hint: Optional[str] = None) -> Optional[str]:
    """Open editor with content, return modified content or None if cancelled.

    Args:
        initial_content: Initial text to populate the editor with
        entry_type: Optional entry kind (journal, memory, todo, etc.) for filename
        filename_hint: Optional hint for filename (e.g., entry @name or content snippet)

    Returns:
        Modified content as string, or None if cancelled/unchanged/empty
    """
    # Build descriptive prefix for temp file
    prefix = 'tj_'
    if entry_type and filename_hint:
        from tj.commands.common import ENTRY_TYPE_ABBREV

        # Get type abbreviation
        type_abbrev = ENTRY_TYPE_ABBREV.get(entry_type, entry_type)

        # Sanitize hint: lowercase, remove special chars, truncate to 20 chars
        sanitized = filename_hint.lower()
        sanitized = ''.join(c if c.isalnum() or c in ' _-' else '_' for c in sanitized)
        sanitized = sanitized.replace(' ', '_')
        sanitized = sanitized.strip('_')[:20].strip('_')
        prefix = f'tj_{type_abbrev}-{sanitized}_'

    # Create temp file with descriptive prefix and .md suffix for syntax highlighting
    fd, temp_path = tempfile.mkstemp(prefix=prefix, suffix='.md', text=True)

    try:
        # Write initial content and close file descriptor
        with os.fdopen(fd, 'w') as f:
            f.write(initial_content)

        # Get editor command (may contain args, e.g., "emacs -nw")
        editor = get_editor_command()
        editor_parts = editor.split()

        # Build command with wait flag for known editors
        editor_cmd = editor_parts + [temp_path]

        # Add --wait flag for editors that need it
        editor_name = os.path.basename(editor_parts[0]).lower()
        if editor_name in ['bbedit', 'mate', 'subl', 'code']:
            # BBEdit, TextMate, Sublime, VS Code need --wait
            editor_cmd = editor_parts + ['--wait', temp_path]
        elif editor_name == 'nano':
            # nano blocks by default
            editor_cmd = editor_parts + [temp_path]

        # Launch editor (blocks until user closes)
        try:
            from tj.options import debug_mark
            debug_mark("editor_launch")

            # Disable focus reporting to suppress escape sequences (use __stdout__ to bypass buffer)
            import sys
            sys.__stdout__.write('\033[?1004l')
            sys.__stdout__.flush()

            result = subprocess.call(editor_cmd)

            # Re-enable focus reporting
            sys.__stdout__.write('\033[?1004h')
            sys.__stdout__.flush()
        except FileNotFoundError:
            print(f"Error: Editor '{editor}' not found")
            return None
        except Exception as e:
            traceback.print_exc()
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
        except OSError:
            pass


def handle_editor_create(entry_type: Optional[str] = None, extra_args: Optional[list] = None) -> None:
    """Handle creating entry via editor.

    Args:
        entry_type: Optional entry type (ai, todo, profile, bookmark, calendar)
        extra_args: Optional extra arguments like @name =context :tag
    """
    # Extract name from extra_args for filename hint
    filename_hint = 'new_entry'
    if extra_args:
        for arg in extra_args:
            if arg.startswith('@'):
                filename_hint = arg[1:]  # Use name without @
                break

    # Show what we're creating (bypass buffer to show immediately)
    import sys
    if extra_args:
        metadata_str = " ".join(extra_args)
        print(f"Creating new entry: {metadata_str}", file=sys.__stdout__, flush=True)
    else:
        entry_type_display = entry_type if entry_type else "memory"
        print(f"Creating new {entry_type_display} entry...", file=sys.__stdout__, flush=True)

    content = open_editor(entry_type=entry_type, filename_hint=filename_hint)

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

    # Strip leading/trailing whitespace from full content
    full_content = full_content.strip()

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


