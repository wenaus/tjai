"""Opt-in TJAI mailbox receiver for one explicitly selected local LLM session."""

import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_call
from claude_client import send as send_claude
from codex_client import CodexClient


async def call(tool, **arguments):
    def request():
        try:
            result = json.loads(mcp_call.call(tool, arguments, timeout=35))
        except SystemExit as exc:
            raise RuntimeError(str(exc)) from exc
        if isinstance(result, dict) and "error" in result:
            raise RuntimeError(result["error"])
        return result
    return await asyncio.to_thread(request)


def emit(**data):
    print(json.dumps(data), flush=True)


async def target_state(args):
    if args.client == "codex":
        async with CodexClient(args.socket) as client:
            if args.native_id not in await client.loaded_threads():
                raise RuntimeError("Target is not loaded in the selected Codex runtime")
            thread = (await client.call("thread/read", {"threadId": args.native_id, "includeTurns": False}))["thread"]
            return "active" if thread["status"]["type"] == "active" else "idle"
    # Claude's registry is written by the session, not inferred from its transcript.
    registry = Path.home() / ".claude" / "sessions"
    for path in registry.glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if record.get("sessionId") == args.native_id and record.get("messagingSocketPath") == args.socket:
            os.kill(record["pid"], 0)
            return "active" if record.get("status") in {"busy", "active", "working"} else "idle"
    raise RuntimeError("Selected Claude session is not in the live local registry")


async def deliver(args, registration, message):
    session_id = registration["id"]
    claim = await call("record_delivery", session_id=session_id, message_id=message["message_id"],
                       state="uncertain", claim=True, detail="Adapter reserved delivery; client receipt not yet known")
    if not claim.get("claimed"):
        return
    instructions = (
        f"Your TJAI session ID is {session_id}. This peer message has TJAI message ID "
        f"{message['message_id']}. After considering it, call acknowledge_message "
        "with those IDs. If a reply is needed, use TJAI send_message with "
        f"sender_id={session_id}, recipient_id={message['sender_id']}, "
        f"reply_to={message['message_id']} and a new UUID message_id. A reply "
        "acknowledges receipt. Do not reply solely to acknowledge.\n"
        f"Reply requested: {message['reply_requested']}\n\n{message['content']}"
    )
    try:
        if args.client == "codex":
            async with CodexClient(args.socket) as client:
                receipt = await client.send(args.native_id, instructions,
                                            message["sender_id"], message["message_id"])
        else:
            receipt = await asyncio.to_thread(send_claude, args.socket, args.native_id,
                          instructions, message["sender_id"], message["message_id"])
    except Exception as exc:
        # No blind retry: the client may have accepted before a connection failed.
        await call("record_delivery", session_id=session_id, message_id=message["message_id"],
                   state="uncertain", detail=f"Client delivery could not be confirmed: {type(exc).__name__}: {exc}"[:2000])
        emit(message_id=message["message_id"], state="uncertain", error=str(exc))
        return
    await call("record_delivery", session_id=session_id, message_id=message["message_id"], state=receipt["state"])
    emit(message_id=message["message_id"], state=receipt["state"])


async def run(args):
    state = await target_state(args)
    # One receiver per client/host/native session on this machine. The server
    # also atomically claims each delivery to protect against overlapping hosts.
    import hashlib
    identity = hashlib.sha256(f"{args.client}:{args.host}:{args.native_id}".encode()).hexdigest()
    directory = Path.home() / ".tjai" / "comms"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = directory / f"{identity}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        registration = await call("register_session", native_id=args.native_id, name=args.name,
            host=args.host, client=args.client, model=args.model, cwd=args.cwd,
            resources=args.resource, delivery="codex_app_server" if args.client == "codex" else "claude_socket", state=state)
        emit(session=registration)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in [signal.SIGINT, signal.SIGTERM]:
            loop.add_signal_handler(sig, stop.set)
        try:
            retry_delay = 1
            while not stop.is_set():
                try:
                    state = await target_state(args)
                    await call("heartbeat_session", session_id=registration["id"], state=state)
                    messages = await call("wait_messages", session_id=registration["id"], wait_seconds=25, limit=20)
                    for message in messages:
                        if stop.is_set():
                            break
                        await deliver(args, registration, message)
                    retry_delay = 1
                except (OSError, ValueError, RuntimeError) as exc:
                    emit(state="reconnecting", error=str(exc), retry_seconds=retry_delay)
                    if args.once:
                        raise
                    try:
                        await call("heartbeat_session", session_id=registration["id"], state="offline")
                    except (OSError, RuntimeError):
                        pass  # Discovery expires automatically without heartbeats.
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=retry_delay)
                    except TimeoutError:
                        pass
                    retry_delay = min(retry_delay * 2, 25)
                if args.once:
                    break
        finally:
            try:
                await call("heartbeat_session", session_id=registration["id"], state="offline")
            except (OSError, RuntimeError) as exc:
                emit(state="stopped", error=f"Offline heartbeat failed; registration will expire: {exc}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=["codex", "claude"], required=True)
    parser.add_argument("--native-id", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--resource", action="append", default=[])
    parser.add_argument("--once", action="store_true", help="Receive one bounded batch, then stop")
    try:
        asyncio.run(run(parser.parse_args()))
    except (OSError, ValueError, RuntimeError) as exc:
        emit(error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
