"""AI Agent command - launch a detached AI agent with mandatory guidance.

tj agent is for standalone autonomous tasks that do NOT feed results back to the
caller's session: cron jobs, periodic reflection, async analysis, briefings.
Results are written to a tjai entry, not returned to the invoking process.

DO NOT use tj agent for research that informs the current conversation or session.
For that, use Claude Code's internal Task tool subagents, which return results
directly into the session context where they can inform next steps.

tj agent = fire-and-forget, reports to tjai entry
Task subagent = in-session, feeds back into current context
"""

import json
import os
import re
import shutil
import sys
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from tj.repository import Entry
from tj.repository_factory import RepositoryFactory
from tj.state import get_state, save_state
from tj.uuid7 import uuid7


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
    """Handle 'tj agent' command — launch a detached, autonomous AI agent.

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

    model = os.environ.get('TJAI_AGENT_MODEL', 'opus')
    codex_path = None
    claude_path = None
    if _is_codex_model(model):
        try:
            codex_path = _find_codex()
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
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
    audience = 'openai' if codex_path else 'anthropic'
    guidance_text = _fetch_guidance(context, audience)
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

    if codex_path:
        _launch_codex(codex_path, system_prompt, prompt, entry_id)
    else:
        _launch_claude(claude_path, system_prompt, prompt, entry_id)


def _fetch_guidance(context: Optional[str], audience: str) -> str:
    """Fetch AI guidance from tjai, formatted as text."""
    try:
        repository = RepositoryFactory.get_repository()
        all_ai = repository.query_entries(kind='ai')
        active = [e for e in all_ai if not getattr(e, 'deleted_at', None)]

        universal = []
        specific = []

        for entry in active:
            entry_data = entry.data if isinstance(entry.data, dict) else {}
            audiences = entry_data.get('audiences')
            if audiences and audience not in audiences:
                continue
            is_universal = not entry.context
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
    entry_id = str(uuid7())
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

    Per-action opt-in (TJAI_PROMPT_IS_SYSTEM=1): the action's ai_prompt IS
    the laser-targeted ground-zero instruction and is used verbatim as the
    system prompt body, with guidance prepended. No generic wrapper, no
    MANDATORY-CONCLUSION-into-tracking-entry — those silently veto the
    action's own output requirements. Opt-in via `prompt_is_system_prompt:
    true` on the action's data. Default behavior for actions and ad-hoc
    `tj agent` runs is unchanged. Diagnosed 2026-04-25 from the ideation
    output regression that started with the Opus 4.7 release on 2026-04-16:
    4.7 obeys the system prompt strictly where 4.6 was loose enough for
    the user prompt's rich-output requirements to win.

    If custom_prompt is provided (e.g. research system prompt), it replaces
    the default agent behavior. Guidance and operational rules are still
    included.
    """
    mcp_discovery_rule = """MCP TOOL DISCOVERY:
- If this task names a tjai MCP tool but that tool is not currently exposed as
  a callable function, use the available tool-discovery mechanism to load/search
  for that exact tool before substituting other tools. In Codex this may appear
  as tool_search.
- Server instruction text is not enough; use the actual callable tool when the
  task asks for it. For structured evidence windows, prefer direct
  search_entries(kind, context, start_date, max_content_length) access once
  loaded."""

    if os.environ.get('TJAI_PROMPT_IS_SYSTEM'):
        prompt = f"{mcp_discovery_rule}\n\n{original_prompt}"
        if guidance:
            return f"{guidance}\n\n---\n\n{prompt}"
        return prompt

    if custom_prompt:
        # The custom_prompt path is for actions that supply their own system
        # prompt via `system_prompt_entry_id` (research-agent, llm-assessment-mcp).
        # Those custom prompts set their own format and depth requirements
        # (e.g. research-system-prompt-claude mandates 3000-6000 words and
        # specific sections). Do NOT inject a generic "Be concise. No preamble."
        # here — under Opus 4.7 it overrides the custom prompt's depth mandate
        # and collapses output ~10x. Keep only the universally-needed
        # operational rules (tool routing + error visibility) and let the
        # custom prompt own the rest.
        return f"""{guidance}

OPERATIONAL RULES:
- Use mcp__tjai__ tools for all tjai data access.
- If a named tjai MCP tool is not callable yet, use tool discovery to load it
  before falling back to substitutes.
- If you encounter any error, append it visibly to tracking entry {entry_id} via mcp__tjai__append_entry_content. Never fail silently.

{mcp_discovery_rule}

{custom_prompt}"""

    return f"""You are a tjai research and task agent working for Torre Wenaus, a physicist and software developer at BNL. You have access to his personal knowledge base via MCP tools. Call get_profile() to understand the user, following next_offset until complete=true. You are truthful, thorough, and addicted to researching facts rather than assuming.

{guidance}

OPERATIONAL RULES:
- Use mcp__tjai__ tools for all tjai data access.
- If a named tjai MCP tool is not callable yet, use tool discovery to load it
  before falling back to substitutes.
- Be concise and factual. No preamble.
- If you encounter any error, append it to entry {entry_id} via mcp__tjai__append_entry_content. Never fail silently.
- mcp__tjai__append_entry_content always preserves existing content — do NOT use replace_entry_content, which clobbers.

{mcp_discovery_rule}

MANDATORY CONCLUSION:
When your task is complete, you MUST call mcp__tjai__append_entry_content on entry {entry_id} with:
[RESULT]
<your findings formatted in markdown — use headings, bullets, formatted links, etc.>
This is not optional. The entry is your report-back mechanism. Existing content is preserved automatically — you never need to read-then-rewrite."""


CODEX_PATHS = [
    os.environ.get('TJAI_CODEX_PATH', ''),
    '/home/admin/.nvm/versions/node/v24.13.1/bin/codex',
    '/usr/local/bin/codex',
]
CODEX_MCP_TOOLS = [
    'get_server_instructions',
    'get_calendar',
    'get_profile',
    'get_ai_guidance',
    'list_contexts',
    'create_entry',
    'get_todos',
    'get_memories',
    'get_bookmarks',
    'get_dialog',
    'get_logs',
    'search_entries',
    'get_named_entries',
    'get_entry',
    'get_entry_by_entry_id',
    'edit_entry_metadata',
    'replace_entry_content',
    'replace_text_in_entry',
    'replace_section_in_entry',
    'edit_entry',
    'append_entry_content',
    'copy_calendar_entry',
    'change_entry_kind',
    'delete_entry',
    'run_action',
    'create_goal',
    'get_goal',
    'create_relation',
    'edit_relation',
    'delete_relation',
    'get_relations',
    'get_relation_graph',
    'get_entry_versions',
    'restore_version',
]


def _toml_literal(value):
    """Return a simple TOML literal suitable for Codex -c key=value."""
    return json.dumps(value)


def _find_codex():
    """Find the codex CLI binary."""
    for path in CODEX_PATHS:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    fallback = shutil.which('codex')
    if fallback:
        return fallback
    raise RuntimeError("codex CLI not found at: " + ", ".join(p for p in CODEX_PATHS if p))


def _is_codex_model(model):
    """Return True when this action should run through codex exec."""
    runner = os.environ.get('TJAI_AGENT_RUNNER', '').lower()
    if runner == 'codex':
        return True
    return model.startswith('gpt-') or model.startswith('codex')


def _codex_mcp_config_args():
    """Build Codex -c overrides for the tjai HTTP MCP server."""
    env_name = 'TJAI_CODEX_MCP_TOKEN'
    args = [
        '-c', f'mcp_servers.tjai.url={_toml_literal("https://etaverse.com/tjai/mcp/")}',
        '-c', f'mcp_servers.tjai.bearer_token_env_var={_toml_literal(env_name)}',
        '-c', 'mcp_servers.tjai.required=true',
        '-c', 'mcp_servers.tjai.default_tools_approval_mode="approve"',
        '-c', 'mcp_servers.tjai.tool_timeout_sec=300',
    ]
    for tool_name in CODEX_MCP_TOOLS:
        args += [
            '-c',
            f'mcp_servers.tjai.tools.{tool_name}.approval_mode="approve"',
        ]
    return args, env_name


def _build_codex_command(codex_path, model, effort, output_file):
    """Build a non-interactive Codex command matching corun-ai's pattern."""
    mcp_args, _ = _codex_mcp_config_args()
    effort_args = []
    if effort:
        if effort == 'max':
            effort = 'xhigh'
        allowed = {'none', 'minimal', 'low', 'medium', 'high', 'xhigh'}
        if effort not in allowed:
            raise RuntimeError(
                f"Unsupported Codex reasoning effort {effort!r}; "
                f"expected one of {', '.join(sorted(allowed))}"
            )
        effort_args = ['-c', f'model_reasoning_effort={_toml_literal(effort)}']
    return [
        codex_path,
        '--ask-for-approval', 'never',
        'exec',
        '--ephemeral',
        '--sandbox', 'workspace-write',
        '--add-dir', '/var/www/tjai/data',
        '--skip-git-repo-check',
        '-m', model,
        '-o', output_file,
        '-c', 'web_search="live"',
        *effort_args,
        *mcp_args,
        '-',
    ]


def _launch_codex(codex_path: str, system_prompt: str, prompt: str, entry_id: str) -> None:
    """Launch codex exec in background. Logs combined output for diagnostics."""
    import shlex

    model = os.environ.get('TJAI_AGENT_MODEL', 'gpt-5.6-sol')
    effort = os.environ.get('TJAI_AGENT_EFFORT', 'xhigh')
    timeout_secs = int(os.environ.get('TJAI_AGENT_TIMEOUT', '0'))
    action_id = os.environ.get('TJAI_ACTION_ID')

    user_prompt = ("Begin executing your task per the system prompt now."
                   if os.environ.get('TJAI_PROMPT_IS_SYSTEM') else prompt)
    combined_prompt = (
        "SYSTEM INSTRUCTIONS (follow these for all responses):\n"
        f"{system_prompt}\n\n"
        "USER REQUEST:\n"
        f"{user_prompt}"
    )

    work_dir = tempfile.mkdtemp(prefix='tjai-codex-')
    prompt_file = os.path.join(work_dir, 'prompt.txt')
    output_file = os.path.join(work_dir, 'codex-output.md')
    with open(prompt_file, 'w', encoding='utf-8') as f:
        f.write(combined_prompt)
    os.chmod(prompt_file, 0o600)

    cmd = _build_codex_command(codex_path, model, effort, output_file)

    env = os.environ.copy()
    env['HOME'] = os.environ.get('HOME', '/home/admin')
    base_path = os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')
    required_paths = [
        '/home/admin/.nvm/versions/node/v24.13.1/bin',
        '/home/admin/.local/bin',
    ]
    env['PATH'] = ':'.join(required_paths + [base_path])
    env['PYTHONIOENCODING'] = 'utf-8'
    env['LANG'] = 'C.UTF-8'
    env['LC_ALL'] = 'C.UTF-8'
    env.pop('OPENAI_API_KEY', None)
    env.pop('CODEX_API_KEY', None)
    env.pop('CLAUDECODE', None)
    token = env.get('TJAI_MCP_TOKEN', '').strip()
    if token:
        env['TJAI_CODEX_MCP_TOKEN'] = token

    # Record model/effort in tracking entry metadata
    try:
        repository = RepositoryFactory.get_repository()
        entry = repository.get_entry(entry_id)
        if entry:
            existing = entry.data if isinstance(entry.data, dict) else {}
            existing['model'] = model
            existing['effort'] = 'xhigh' if effort == 'max' else effort
            existing['runner'] = 'codex'
            repository.update_entry(entry_id, data=existing, is_dirty=True)
    except Exception as e:
        print(f"Error writing agent metadata to entry: {e}", file=sys.stderr)

    _append_to_entry(entry_id, "LAUNCHED codex")

    if action_id:
        scripts_dir = Path(__file__).resolve().parent.parent.parent / 'scripts'
        completion_cmd = f'{sys.executable} {scripts_dir}/agent_complete.py {shlex.quote(action_id)}'
        codex_cmd = shlex.join(cmd)
        if timeout_secs > 0:
            codex_cmd = f'timeout {timeout_secs} {codex_cmd}'
        shell_cmd = (
            f'ERRFILE=$(mktemp /tmp/tjai-agent-XXXXXX.err) ; '
            f'{codex_cmd} < {shlex.quote(prompt_file)} >"$ERRFILE" 2>&1 ; '
            f'CODE=$? ; '
            f'{completion_cmd} $CODE "$ERRFILE" ; '
            f'rm -rf {shlex.quote(work_dir)}'
        )
        subprocess.Popen(
            ['bash', '-c', shell_cmd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
            cwd='/var/www/tjai' if os.path.isdir('/var/www/tjai') else os.getcwd(),
        )
    else:
        if timeout_secs > 0:
            cmd = ['timeout', str(timeout_secs)] + cmd
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
            env=env,
            cwd='/var/www/tjai' if os.path.isdir('/var/www/tjai') else os.getcwd(),
        )
        proc.stdin.write(combined_prompt)
        proc.stdin.close()

    print("Agent launched")


def _launch_claude(claude_path: str, system_prompt: str, prompt: str, entry_id: str) -> None:
    """Launch claude -p in background. Logs stderr to file for diagnostics."""
    import shlex

    model = os.environ.get('TJAI_AGENT_MODEL', 'opus')
    if _is_codex_model(model):
        _launch_codex(_find_codex(), system_prompt, prompt, entry_id)
        return

    effort = os.environ.get('TJAI_AGENT_EFFORT', 'xhigh')
    timeout_secs = int(os.environ.get('TJAI_AGENT_TIMEOUT', '0'))

    if not os.environ.get('TJAI_AGENT_MODEL'):
        print(f"WARNING: TJAI_AGENT_MODEL not set, defaulting to {model}", file=sys.stderr)
    if not os.environ.get('TJAI_AGENT_EFFORT'):
        print(f"WARNING: TJAI_AGENT_EFFORT not set, defaulting to {effort}", file=sys.stderr)

    # When the per-action opt-in is set, the action's ai_prompt has been
    # used as the system prompt (see _build_system_prompt). User-prompt
    # slot becomes a minimal kick-off so the model starts immediately and
    # the system prompt isn't duplicated. Default behavior unchanged.
    user_prompt = ("Begin executing your task per the system prompt now."
                   if os.environ.get('TJAI_PROMPT_IS_SYSTEM') else prompt)

    cmd = [
        claude_path,
        '-p', user_prompt,
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
