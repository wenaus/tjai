"""TJAI mailbox receiver for one explicitly selected local LLM session."""

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
from presentation import instruction_marker, mark_instructions, message_text, session_instructions


class TargetGone(RuntimeError):
    pass


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


def transcript_model(path, tail_bytes=262144):
    """The model that answered the latest assistant turn of a Claude transcript.

    Claude's session registry carries no model and /model changes it mid-session,
    so the transcript is the live source; empty when nothing can be read."""
    if not path:
        return ""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - tail_bytes))
            lines = handle.read().splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("type") == "assistant":
            model = (record.get("message") or {}).get("model")
            if isinstance(model, str) and model and not model.startswith("<"):
                return model
    return ""


async def target_state(args):
    if getattr(args, "pid", None):
        os.kill(args.pid, 0)
    if args.client == "codex_queue":
        return "unknown"  # The embedded CLI exposes no live turn-status API.
    if args.client == "codex":
        from codex_client import CodexClient
        async with CodexClient(args.socket) as client:
            if args.native_id not in await client.loaded_threads():
                raise TargetGone("Target is not loaded in the selected Codex runtime")
            thread = (await client.call("thread/read", {"threadId": args.native_id, "includeTurns": False}))["thread"]
            if thread.get("threadSource") == "system":
                raise TargetGone("Internal Codex housekeeping threads are not peer sessions")
            args.name = thread.get("name") or args.name
            args.model = thread.get("model") or args.model
            args.cwd = thread.get("cwd") or args.cwd
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
            args.name = record.get("name") or args.name
            args.model = transcript_model(args.transcript) or args.model
            return "active" if record.get("status") in {"busy", "active", "working"} else "idle"
    raise TargetGone("Selected Claude session is not in the live local registry")


async def deliver(args, registration, message):
    session_id = registration["id"]
    claim = await call("record_delivery", session_id=session_id, message_id=message["message_id"],
                       state="uncertain", claim=True, detail="Adapter reserved delivery; client receipt not yet known")
    if not claim.get("claimed"):
        return
    instructions = message_text(message)
    needs_instructions = not instruction_marker(session_id).exists()
    if needs_instructions:
        instructions += "\n\nTJAI session instructions (once):\n" + session_instructions(session_id)
    try:
        if args.client == "codex":
            from codex_client import CodexClient
            async with CodexClient(args.socket) as client:
                receipt = await client.send(args.native_id, instructions,
                                            message["sender_id"], message["message_id"])
        elif args.client == "codex_queue":
            from codex_queue import send
            receipt = await asyncio.to_thread(send, args.native_id, instructions,
                                              message["sender_id"], message["message_id"])
        else:
            receipt = await asyncio.to_thread(send_claude, args.socket, args.native_id,
                          instructions, message["sender_id"], message["message_id"],
                          os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN"))
    except Exception as exc:
        # No blind retry: the client may have accepted before a connection failed.
        await call("record_delivery", session_id=session_id, message_id=message["message_id"],
                   state="uncertain", detail=f"Client delivery could not be confirmed: {type(exc).__name__}: {exc}"[:2000])
        emit(message_id=message["message_id"], state="uncertain", error=str(exc))
        return
    if needs_instructions:
        mark_instructions(session_id)
    await call("record_delivery", session_id=session_id, message_id=message["message_id"], state=receipt["state"])
    emit(message_id=message["message_id"], state=receipt["state"])


async def run(args):
    # SessionStart can run before the native registry/socket is fully published.
    for attempt in range(30):
        try:
            state = await target_state(args)
            break
        except (OSError, ValueError, RuntimeError):
            if attempt == 29 or args.once:
                raise
            await asyncio.sleep(1)
    # One receiver per client/host/native session on this machine. The server
    # also atomically claims each delivery to protect against overlapping hosts.
    import hashlib
    client_name = "codex" if args.client == "codex_queue" else args.client
    identity = hashlib.sha256(f"{client_name}:{args.host}:{args.native_id}".encode()).hexdigest()
    directory = Path.home() / ".tjai" / "comms"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = directory / f"{identity}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        delivery = {"codex": "codex_app_server", "codex_queue": "codex_queue", "claude": "claude_socket"}[args.client]
        retry_delay = 1
        while True:
            try:
                registration = await call("register_session", native_id=args.native_id, name=args.name,
                    host=args.host, client=client_name, model=args.model, cwd=args.cwd,
                    resources=args.resource, delivery=delivery, state=state)
                break
            except (OSError, RuntimeError) as exc:
                if args.once:
                    raise
                emit(state="registering", error=str(exc), retry_seconds=retry_delay)
                await asyncio.sleep(retry_delay)
                state = await target_state(args)  # Stop if the native owner disappeared.
                retry_delay = min(retry_delay * 2, 25)
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
                    await call("heartbeat_session", session_id=registration["id"], state=state,
                               name=args.name, model=args.model, cwd=args.cwd)
                    messages = await call("wait_messages", session_id=registration["id"], wait_seconds=25, limit=20)
                    for message in messages:
                        if stop.is_set():
                            break
                        await deliver(args, registration, message)
                    retry_delay = 1
                except TargetGone:
                    break
                except (OSError, ValueError, RuntimeError) as exc:
                    if getattr(args, "pid", None):
                        try:
                            os.kill(args.pid, 0)
                        except ProcessLookupError:
                            break
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
    parser.add_argument("--client", choices=["codex", "claude", "codex_queue"], required=True)
    parser.add_argument("--native-id", required=True)
    parser.add_argument("--socket", default="")
    parser.add_argument("--pid", type=int, help="Stop when this owning native process exits")
    parser.add_argument("--host", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--transcript", default="", help="Claude transcript; refreshes --model each heartbeat")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--resource", action="append", default=[])
    parser.add_argument("--once", action="store_true", help="Receive one bounded batch, then stop")
    try:
        args = parser.parse_args()
        if args.client == "codex_queue" and not args.pid:
            parser.error("codex_queue requires the live owning --pid")
        if args.client != "codex_queue" and not args.socket:
            parser.error("native socket delivery requires --socket")
        asyncio.run(run(args))
    except (OSError, ValueError, RuntimeError) as exc:
        emit(error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
