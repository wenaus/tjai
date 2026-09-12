"""Deliver a peer message to an explicitly selected local Claude Code inbox.

The socket is documented by Anthropic; the user-frame format was checked against
Claude Code 2.1.268. A successful write is not a delivery acknowledgment.
"""

import argparse
import json
import os
from pathlib import Path
import socket
import stat
import sys
import uuid

from presentation import envelope


def send(socket_path, session_id, text, sender, message_id, token=None):
    path = Path(socket_path)
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("Select a Claude inbox socket owned by the current user")
    if info.st_mode & 0o077:
        raise ValueError("The Claude inbox socket must be private to its owner")
    if not text.strip():
        raise ValueError("Message is empty")
    uuid.UUID(session_id)
    uuid.UUID(message_id)
    frame = {"type": "user", "session_id": session_id, "uuid": message_id,
             "msg_id": message_id, "from": sender, "priority": "now",
             "message": {"role": "user", "content": envelope(text, message_id)}}
    payload = (json.dumps(frame) + "\n").encode()
    if len(payload) > 65536:
        raise ValueError("Peer messages are limited to 64 KiB")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(5)
        stream.connect(str(path))
        if token:
            stream.sendall((json.dumps({"type": "auth", "token": token}) + "\n").encode())
        stream.sendall(payload)
    return {"message_id": message_id, "session_id": session_id,
            "state": "written_to_transport"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--message-file", required=True, help="UTF-8 file, or - for stdin")
    parser.add_argument("--message-id", default=str(uuid.uuid4()))
    args = parser.parse_args()
    try:
        text = sys.stdin.read() if args.message_file == "-" else Path(args.message_file).read_text()
        print(json.dumps(send(args.socket, args.session, text, args.sender, args.message_id)))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
