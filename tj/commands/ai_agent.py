"""AI Agent command - launch a DETACHED Claude instance with mandatory guidance.

tj agent is for standalone autonomous tasks that do NOT feed results back to the
caller's session: cron jobs, periodic reflection, async analysis, briefings.
Results are written to a tjai entry, not returned to the invoking process.

DO NOT use tj agent for research that informs the current conversation or session.
For that, use Claude Code's internal Task tool subagents, which return results
directly into the session context where they can inform next steps.

tj agent = fire-and-forget, reports to tjai entry
Task subagent = in-session, feeds back into current context
"""

import os
import shutil
import sys
import subprocess
import uuid
from pathlib import Path
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
    """Handle 'tj agent' command — launch a detached, autonomous Claude instance.

    This is for fire-and-forget tasks that need project context (MCP, AI guidance)
    but do NOT need to feed results back into the current session. The agent writes
    its output to a tjai entry. Use cases: cron jobs, periodic reflection, briefings.

    NOT for research that informs the current conversation — use internal Task
    tool subagents for that.

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
        fallback = os.path.expanduser('~/.local/bin/claude')
        if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
            claude_path = fallback
        else:
            print("Error: 'claude' CLI not found in PATH or ~/.local/bin", file=sys.stderr)
            sys.exit(1)

    # Fetch AI guidance
    guidance_text = _fetch_guidance(context)
    if not guidance_text:
        print("Warning: No AI guidance found.", file=sys.stderr)

    # Create tjai entry for tracking
    entry_id = _create_tracking_entry(prompt, context)
    print(f"Tracking: [[tracking:{entry_id}]]  [{prompt[:60]}]")
    print(f"TRACKING_ID={entry_id}")

    # Build system prompt (custom prompt overrides default agent behavior)
    custom_prompt = os.environ.get('TJAI_SYSTEM_PROMPT')
    system_prompt = _build_system_prompt(guidance_text, entry_id, prompt,
                                         custom_prompt=custom_prompt)

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
    entry_id = str(uuid.uuid7())
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

    # Force an immediate sync push. Without this the tracking entry sits
    # in local SQLite with is_dirty=1 and only reaches Postgres on the
    # tj_agent daemon's next 30s sync cycle. Meanwhile the caller
    # launches a Claude subprocess within seconds — when that subprocess
    # queries via MCP (which reads Postgres), the entry isn't there yet
    # and the agent falls back to creating a duplicate "standalone
    # result" entry. Same entry_id → duplicate rows. Diagnosed
    # 2026-04-18: affected ideation 04-16, 04-17, 04-18.
    #
    # Wrapped to swallow errors — a transient sync failure must not
    # break the agent run. The worst case without the push is the old
    # failure mode (30s sync lag) which the caller already tolerates.
    try:
        from tj_agent.sync import push_dirty_entries
        push_dirty_entries()
    except Exception as e:
        print(f"Warning: sync push after tracking-entry create failed: {e}",
              file=sys.stderr)

    return entry_id


def _build_system_prompt(guidance: str, entry_id: str, original_prompt: str,
                         custom_prompt: str = None) -> str:
    """Build the system prompt for the claude instance.

    If custom_prompt is provided (e.g. research system prompt), it replaces the
    default agent behavior. Guidance and operational rules are still included.
    """
    if custom_prompt:
        return f"""{guidance}

OPERATIONAL RULES:
- Use mcp__tjai__ tools for all tjai data access.
- Be concise and factual. No preamble.
- If you encounter any error, append it visibly to tracking entry {entry_id} via mcp__tjai__append_entry_content. Never fail silently.

{custom_prompt}"""

    return f"""You are a tjai research and task agent working for Torre Wenaus, a physicist and software developer at BNL. You have access to his personal knowledge base via MCP tools. Call get_profile() to understand the user. You are truthful, thorough, and addicted to researching facts rather than assuming.

{guidance}

OPERATIONAL RULES:
- Use mcp__tjai__ tools for all tjai data access.
- Be concise and factual. No preamble.
- If you encounter any error, append it to entry {entry_id} via mcp__tjai__append_entry_content. Never fail silently.
- mcp__tjai__append_entry_content always preserves existing content — do NOT use replace_entry_content, which clobbers.

MANDATORY CONCLUSION:
When your task is complete, you MUST call mcp__tjai__append_entry_content on entry {entry_id} with:
[RESULT]
<your findings formatted in markdown — use headings, bullets, formatted links, etc.>
This is not optional. The entry is your report-back mechanism. Existing content is preserved automatically — you never need to read-then-rewrite."""


def _launch_claude(claude_path: str, system_prompt: str, prompt: str, entry_id: str) -> None:
    """Launch claude -p in background. Logs stderr to file for diagnostics."""
    import shlex

    model = os.environ.get('TJAI_AGENT_MODEL', 'opus')
    effort = os.environ.get('TJAI_AGENT_EFFORT', 'xhigh')
    timeout_secs = int(os.environ.get('TJAI_AGENT_TIMEOUT', '0'))

    if not os.environ.get('TJAI_AGENT_MODEL'):
        print(f"WARNING: TJAI_AGENT_MODEL not set, defaulting to {model}", file=sys.stderr)
    if not os.environ.get('TJAI_AGENT_EFFORT'):
        print(f"WARNING: TJAI_AGENT_EFFORT not set, defaulting to {effort}", file=sys.stderr)

    cmd = [
        claude_path,
        '-p', prompt,
        '--system-prompt', system_prompt,
        '--output-format', 'text',
        '--model', model,
        '--effort', effort,
    ]

    env = os.environ.copy()
    env.pop('CLAUDECODE', None)
    env.pop('ANTHROPIC_API_KEY', None)  # Force subscription auth, not API

    # Record model/effort in tracking entry metadata
    try:
        repository = RepositoryFactory.get_repository()
        entry = repository.get_entry(entry_id)
        if entry:
            existing = entry.data if isinstance(entry.data, dict) else {}
            existing['model'] = model
            existing['effort'] = effort
            repository.update_entry(entry_id, data=existing, is_dirty=True)
    except Exception as e:
        print(f"Error writing agent metadata to entry: {e}", file=sys.stderr)

    _append_to_entry(entry_id, "LAUNCHED")

    action_id = os.environ.get('TJAI_ACTION_ID')
    if action_id:
        # Wrap: capture stderr to temp file, run claude, pass stderr file to agent_complete
        scripts_dir = Path(__file__).resolve().parent.parent.parent / 'scripts'
        completion_cmd = f'{sys.executable} {scripts_dir}/agent_complete.py {shlex.quote(action_id)}'
        claude_cmd = shlex.join(cmd)
        if timeout_secs > 0:
            claude_cmd = f'timeout {timeout_secs} {claude_cmd}'
        # Capture all output; agent_complete.py reads and logs it to AppLog
        shell_cmd = (
            f'ERRFILE=$(mktemp /tmp/tjai-agent-XXXXXX.err) ; '
            f'{claude_cmd} >"$ERRFILE" 2>&1 ; '
            f'{completion_cmd} $? "$ERRFILE"'
        )
        subprocess.Popen(
            ['bash', '-c', shell_cmd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    else:
        if timeout_secs > 0:
            cmd = ['timeout', str(timeout_secs)] + cmd
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )

    print(f"Agent launched")
