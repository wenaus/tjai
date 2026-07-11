"""Small, machine-local maintenance actions run by tj_agent."""

import json
import logging
import os
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from tj.config import get_config
from tj.database import APP_DIR
from tj_agent import client

logger = logging.getLogger(__name__)

STATE_FILE = APP_DIR / "local_actions_state.json"
LOG_SOURCE = "local-action"
GIT_TIMEOUT_SECONDS = 300
COMMAND_OUTPUT_LIMIT = 4000


@dataclass
class ActionResult:
    status: str
    message: str


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=20"
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        env=env,
    )


def _git_error(label: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "no output").strip()
    return f"{label} failed with exit {result.returncode}: {detail[-4000:]}"


def update_git_repo(repo_value: str, fetch_url: str | None = None) -> ActionResult:
    """Fetch and fast-forward one clean checkout without merging or stashing."""
    repo = Path(repo_value).expanduser()
    if not (repo / ".git").exists():
        return ActionResult("error", f"Git checkout not found: {repo}")

    try:
        upstream = _run_git(
            repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
        )
        if upstream.returncode:
            return ActionResult("error", _git_error("resolve upstream", upstream))
        upstream_name = upstream.stdout.strip()

        if fetch_url:
            branch = _run_git(repo, "branch", "--show-current")
            merge_ref = _run_git(
                repo, "config", "--get", f"branch.{branch.stdout.strip()}.merge"
            )
            upstream_ref = _run_git(
                repo, "rev-parse", "--symbolic-full-name", "@{u}"
            )
            if branch.returncode or merge_ref.returncode or upstream_ref.returncode:
                failed = next(
                    result
                    for result in (branch, merge_ref, upstream_ref)
                    if result.returncode
                )
                return ActionResult("error", _git_error("resolve fetch ref", failed))
            fetch = _run_git(
                repo,
                "fetch",
                "--quiet",
                fetch_url,
                f"+{merge_ref.stdout.strip()}:{upstream_ref.stdout.strip()}",
            )
        else:
            fetch = _run_git(repo, "fetch", "--quiet", "--prune")
        if fetch.returncode:
            return ActionResult("error", _git_error("git fetch", fetch))

        head = _run_git(repo, "rev-parse", "HEAD")
        remote = _run_git(repo, "rev-parse", "@{u}")
        if head.returncode or remote.returncode:
            failed = head if head.returncode else remote
            return ActionResult("error", _git_error("resolve revisions", failed))

        head_sha = head.stdout.strip()
        remote_sha = remote.stdout.strip()
        if head_sha == remote_sha:
            return ActionResult("current", f"{repo} is current with {upstream_name}")

        merge_base = _run_git(repo, "merge-base", "HEAD", "@{u}")
        if merge_base.returncode:
            return ActionResult("error", _git_error("git merge-base", merge_base))
        base_sha = merge_base.stdout.strip()

        if base_sha == remote_sha:
            return ActionResult(
                "ahead", f"{repo} is ahead of {upstream_name}; no update needed"
            )
        if base_sha != head_sha:
            return ActionResult(
                "error",
                f"{repo} has diverged from {upstream_name}; manual reconciliation required",
            )

        dirty = _run_git(repo, "status", "--porcelain")
        if dirty.returncode:
            return ActionResult("error", _git_error("git status", dirty))
        if dirty.stdout.strip():
            return ActionResult(
                "blocked",
                f"{repo} is behind {upstream_name} but has local changes; update skipped",
            )

        merge = _run_git(repo, "merge", "--ff-only", "--quiet", "@{u}")
        if merge.returncode:
            return ActionResult("error", _git_error("git merge --ff-only", merge))
        return ActionResult(
            "updated",
            f"Updated {repo} from {head_sha[:12]} to {remote_sha[:12]} ({upstream_name})",
        )
    except subprocess.TimeoutExpired as exc:
        return ActionResult("error", f"Git command timed out after {exc.timeout}s for {repo}")
    except OSError as exc:
        return ActionResult("error", f"Could not run git for {repo}: {exc}")


def run_command(action_config: dict) -> ActionResult:
    """Run one configured argv command without invoking a shell."""
    command = action_config.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        return ActionResult("error", "command must be a non-empty JSON string array")

    cwd_value = action_config.get("working_directory")
    cwd = Path(cwd_value).expanduser() if cwd_value else None
    if cwd is not None and not cwd.is_dir():
        return ActionResult("error", f"Working directory not found: {cwd}")

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=int(action_config.get("timeout_seconds", 300)),
        )
    except (OSError, ValueError) as exc:
        return ActionResult("error", f"Could not run command: {exc}")
    except subprocess.TimeoutExpired as exc:
        return ActionResult("error", f"Command timed out after {exc.timeout}s")

    output = (result.stderr or result.stdout or "no output").strip()
    if result.returncode:
        return ActionResult(
            "error",
            f"Command failed with exit {result.returncode}: "
            f"{output[-COMMAND_OUTPUT_LIMIT:]}",
        )
    return ActionResult("success", output[-COMMAND_OUTPUT_LIMIT:] or "Command completed")


def _post_problem(name: str, result: ActionResult) -> None:
    level = "error"
    logger.error("%s: %s", name, result.message)
    location_name = get_config().get("location_name") or socket.gethostname()

    token = os.environ.get("TJAI_API_KEY", "")
    if not token:
        logger.warning("%s: TJAI_API_KEY unavailable; central log skipped", name)
        return

    try:
        client.api_log(
            token=token,
            source=LOG_SOURCE,
            message=f"{name} on {location_name}: {result.message}",
            level=level,
            extra_data={
                "action": name,
                "location_name": location_name,
                "status": result.status,
            },
        )
    except Exception as exc:
        logger.warning("%s: central logging failed: %s", name, exc)


def run_action(name: str, action_config: dict) -> ActionResult:
    """Run one named local action and centrally log only problems."""
    result = run_command(action_config)

    if result.status == "error":
        _post_problem(name, result)
    else:
        logger.info("%s: %s", name, result.message)
    return result


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True))
    temporary.replace(STATE_FILE)


def _scheduled_now(action_config: dict, action_state: dict, now: datetime) -> bool:
    if action_state.get("last_attempt_date") == now.date().isoformat():
        return False

    scheduled_time = str(action_config.get("scheduled_time", "")).strip()
    if len(scheduled_time) != 4 or not scheduled_time.isdigit():
        raise ValueError("scheduled_time must be four digits in HHMM format")
    hour, minute = int(scheduled_time[:2]), int(scheduled_time[2:])
    if hour > 23 or minute > 59:
        raise ValueError("scheduled_time must be a valid local time in HHMM format")
    return (now.hour, now.minute) >= (hour, minute)


def run_due_local_actions(now: datetime | None = None) -> None:
    """Run configured local actions once per local calendar day when due."""
    now = now or datetime.now().astimezone()
    actions = get_config().get("local_actions") or {}
    state = _load_state()

    for name, action_config in actions.items():
        if not isinstance(action_config, dict) or not action_config.get("enabled", False):
            continue

        action_state = state.get(name, {})
        try:
            if not _scheduled_now(action_config, action_state, now):
                continue
            result = run_action(name, action_config)
        except Exception as exc:
            result = ActionResult("error", f"Local action setup failed: {exc}")
            _post_problem(name, result)

        state[name] = {
            "last_attempt": time.time(),
            "last_attempt_date": now.date().isoformat(),
            **asdict(result),
        }
        _save_state(state)
