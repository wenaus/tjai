"""AI Agent command - launch a controlled Claude instance with mandatory guidance."""

import os
import shutil
import sys
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Optional

from tj.repository import Entry
from tj.repository_factory import RepositoryFactory
from tj.state import get_state, save_state


def _timestamp() -> str:
    """Return current timestamp formatted per tjai conventions."""
    import time
    from tj.timezone_manager import format_time_dashboard
    return format_time_dashboard(time.time())


def _append_to_entry(entry_id: str, text: str) -> None:
    """Append timestamped text to a tjai entry."""
    try:
        repository = RepositoryFactory.get_repository()
        entry = repository.get_entry(entry_id)
        if entry:
            new_content = f"{entry.content}\n[{_timestamp()}] {text}"
            repository.update_entry(entry_id, content=new_content, is_dirty=True)
    except Exception as e:
        print(f"Error updating entry: {e}", file=sys.stderr)


def handle_ai_agent(args) -> None:
    """Handle 'tj agent' command.

    Usage:
        tj agent <prompt>           - launch agent with universal guidance
        tj agent =context <prompt>  - launch agent with context-specific guidance
    """
    if not hasattr(args, 'input') or not args.input:
        print("Usage: tj agent [=context] <prompt>", file=sys.stderr)
        return

    # Parse optional context
    context = None
    prompt_parts = list(args.input)
    if prompt_parts[0].startswith('='):
        context = prompt_parts[0][1:]
        if not context:
            print("Error: Empty context name.", file=sys.stderr)
            return
        prompt_parts = prompt_parts[1:]

    if not prompt_parts:
        print("Error: No prompt provided.", file=sys.stderr)
        return

    prompt = " ".join(prompt_parts)

    # Find claude CLI
    claude_path = shutil.which('claude')
    if not claude_path:
        print("Error: 'claude' CLI not found in PATH.", file=sys.stderr)
        return

    # Fetch AI guidance
    guidance_text = _fetch_guidance(context)
    if not guidance_text:
        print("Warning: No AI guidance found.", file=sys.stderr)

    # Create tjai entry for tracking
    entry_id = _create_tracking_entry(prompt, context)
    print(f"Tracking: {entry_id[:8]}  [{prompt[:60]}]")

    # Build system prompt
    system_prompt = _build_system_prompt(guidance_text, entry_id, prompt)

    # Launch claude instance
    _launch_claude(claude_path, system_prompt, prompt, entry_id)


def _fetch_guidance(context: Optional[str]) -> str:
    """Fetch AI guidance from tjai, formatted as text."""
    try:
        repository = RepositoryFactory.get_repository()
        all_ai = repository.query_entries(kind='ai')
        active = [e for e in all_ai if not getattr(e, 'deleted_at', None)]
        tags_by_entry = repository.get_tags_by_entry()

        universal = []
        specific = []

        for entry in active:
            entry_tags = tags_by_entry.get(entry.id, [])
            is_universal = not entry.context and not entry_tags
            if is_universal:
                universal.append(entry)
            elif context and entry.context == context:
                specific.append(entry)

        lines = []
        if universal:
            lines.append("UNIVERSAL GUIDANCE:")
            for e in universal:
                lines.append(f"- {e.content}")
        if specific:
            lines.append(f"\nGUIDANCE FOR CONTEXT '{context}':")
            for e in specific:
                lines.append(f"- {e.content}")

        return "\n".join(lines)
    except Exception as e:
        print(f"Error fetching guidance: {e}", file=sys.stderr)
        return ""


def _create_tracking_entry(prompt: str, context: Optional[str]) -> str:
    """Create a tjai entry to track the agent's work."""
    repository = RepositoryFactory.get_repository()
    entry_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).timestamp()

    state = get_state()

    entry = Entry(
        id=entry_id,
        content=f"[AGENT] {prompt}",
        kind='memory',
        timestamp_created=now,
        timestamp_modified=now,
        context=context or state.get("current_context"),
        is_dirty=True,
    )
    repository.create_entry(entry)
    repository.add_tag(entry_id, 'tjagent')

    state["last_entry_id"] = entry_id
    save_state(state)

    return entry_id


def _build_system_prompt(guidance: str, entry_id: str, original_prompt: str) -> str:
    """Build the system prompt for the claude instance."""
    return f"""You are a tjai agent — a focused, single-task AI worker.

{guidance}

OPERATIONAL RULES:
- Use mcp__tjai__ tools for all tjai data access.
- Be concise and factual. No preamble.
- If you encounter any error, append it to entry {entry_id} via mcp__tjai__edit_entry. Never fail silently.
- When appending to the entry, always preserve all existing content and add your new text after it.

MANDATORY CONCLUSION:
When your task is complete, you MUST call mcp__tjai__edit_entry to APPEND your result to entry {entry_id}.
Read the entry first with mcp__tjai__get_entry, then edit it preserving all existing content and appending:
[RESULT]
<your findings formatted in markdown — use headings, bullets, formatted links, etc.>
This is not optional. The entry is your report-back mechanism."""


def _launch_claude(claude_path: str, system_prompt: str, prompt: str, entry_id: str) -> None:
    """Launch claude -p in background. Logs all outcomes to the tjai entry."""
    cmd = [
        claude_path,
        '-p', prompt,
        '--system-prompt', system_prompt,
        '--output-format', 'text',
        '--model', 'sonnet',
    ]

    env = os.environ.copy()
    env.pop('CLAUDECODE', None)

    _append_to_entry(entry_id, "LAUNCHED")

    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )

    print(f"Agent launched")
