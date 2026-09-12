"""Shared, nonblocking SessionStart integration (standard library only)."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid

from presentation import mark_instructions, session_instructions


DIRECTORY = Path(__file__).resolve().parent


def machine():
    try:
        config = json.loads((Path.home() / ".tjai/config.json").read_text())
    except (OSError, ValueError):
        config = {}
    return config.get("location_name") or socket.gethostname(), config


def native_owner(client):
    """Find our own native ancestor, without inspecting other process environments."""
    pid = os.getppid()
    for _ in range(8):
        try:
            result = subprocess.check_output(["ps", "-p", str(pid), "-o", "ppid=", "-o", "comm="], text=True)
            parent, command = result.strip().split(None, 1)
        except (OSError, ValueError, subprocess.CalledProcessError):
            return None
        if Path(command).name == client:
            return pid
        pid = int(parent)
        if pid <= 1:
            break
    return None


def claude_record(native_id):
    for path in (Path.home() / ".claude/sessions").glob("*.json"):
        try:
            record = json.loads(path.read_text())
            if record.get("sessionId") == native_id:
                os.kill(record["pid"], 0)
                return record
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {}


def start(client, data, host=None, *, context_loaded=False):
    if os.environ.get("TJAI_ACTION_ID") and not os.environ.get("TJAI_COMMS_TEST"):
        return ""  # Scheduled research/action workers are not interactive peers.
    native_id = data.get("session_id") or os.environ.get("CODEX_THREAD_ID", "")
    try:
        uuid.UUID(native_id)
    except (ValueError, TypeError, AttributeError):
        return ""
    location, config = machine()
    host = host or location
    cwd = data.get("cwd") or os.getcwd()
    record = claude_record(native_id) if client == "claude" else {}
    native_socket = (os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET") or record.get("messagingSocketPath", "")) if client == "claude" else os.environ.get("TJAI_CODEX_SOCKET", "")
    pid = record.get("pid") or data.get("owner_pid") or native_owner(client)
    name = record.get("name") or data.get("session_name") or f"{host}-{client}-{native_id[-8:]}"
    model = data.get("model") or ""
    if isinstance(model, dict):
        model = model.get("id") or model.get("display_name") or ""
    transcript = data.get("transcript_path") or ""  # Claude: the live model is read from here
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["tjai:llm", client, host, native_id])))
    transport = client if native_socket else "codex_queue" if client == "codex" else None
    if not transport or not pid:
        return "TJAI communications could not find this client's live inbox/owner; inspect ~/.tjai/comms."
    resources = config.get("comms_resources", [])
    if not isinstance(resources, list):
        resources = []
    resources = sorted(set([f"host:{host}", *resources,
                           *filter(None, os.environ.get("TJAI_COMMS_RESOURCES", "").split(","))]))
    directory = Path.home() / ".tjai/comms"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity = hashlib.sha256(f"{client}:{host}:{native_id}".encode()).hexdigest()
    fd = os.open(directory / f"{identity}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            running = True
        else:
            running = False
    if not running:
        python = os.environ.get("TJAI_COMMS_PYTHON") or sys.executable
        command = [python, str(DIRECTORY / "bridge.py"), "--client", transport,
                   "--native-id", native_id, "--host", host, "--name", name,
                   "--cwd", cwd, "--model", model, "--socket", native_socket, "--pid", str(pid),
                   "--transcript", transcript]
        for resource in resources:
            command.extend(["--resource", resource])
        with open(directory / f"{session_id}.log", "a") as log:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    capability = "immediate native delivery" if native_socket else "native queue delivery at the next input boundary (this embedded CLI cannot be steered)"
    if context_loaded:
        mark_instructions(session_id)
    return (f"## TJAI peer communications\n\nName `{name}`; host `{host}`; {capability}. "
            "Registration and reception run automatically in software. "
            "For shared work, use TJAI messaging so Claude-only native conversations do not omit other clients. "
            + session_instructions(session_id))


if __name__ == "__main__":
    data = json.load(sys.stdin)
    print(start(sys.argv[1], data))
