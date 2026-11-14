"""Deferred delivery for an Antigravity conversation.

Antigravity exposes no inbox the receiver could write into. The receiver
appends each message to a local spool for the conversation; the conversation's
PreInvocation hook (antigravity_hook.py) takes the spool before the next model
call and injects its messages as system messages.
"""

import fcntl
import json
import os
from pathlib import Path
import uuid

from presentation import envelope

DIRECTORY = Path.home() / ".tjai/comms"


def spool(native_id):
    return DIRECTORY / f"agy-{uuid.UUID(native_id)}.inbox"


def name_file(native_id):
    """The name the conversation took with antigravity_hook.py --rename; read at each heartbeat."""
    return DIRECTORY / f"agy-{uuid.UUID(native_id)}.name"


def _locked(native_id):
    DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(DIRECTORY / f"agy-{uuid.UUID(native_id)}.inbox.lock",
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    handle = os.fdopen(fd, "w")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def send(native_id, text, sender, message_id):
    uuid.UUID(message_id)
    line = json.dumps({"message_id": message_id, "sender_id": sender,
                       "text": envelope(text, message_id)}) + "\n"
    with _locked(native_id):
        fd = os.open(spool(native_id), os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as handle:
            handle.write(line)
    return {"message_id": message_id, "thread_id": native_id, "state": "queued_in_client"}


def take(native_id):
    """Every spooled message, removed from the spool; [] when there is none."""
    with _locked(native_id):
        path = spool(native_id)
        try:
            lines = path.read_text().splitlines()
        except FileNotFoundError:
            return []
        path.unlink()
    return [json.loads(line) for line in lines if line.strip()]
