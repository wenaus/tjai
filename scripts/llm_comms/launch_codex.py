"""Opt-in Codex TUI with its own reachable app-server and TJAI receiver.

The runtime and receiver persist after the TUI disconnects, just as native
remote Codex does. Reconnect using the command printed on exit. No existing
session is resumed or moved, and the normal shell launcher is unchanged.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import time

from codex_client import CodexClient


async def receive(args):
    """Wait for the new TUI's first thread, then become its ordinary bridge."""
    from bridge import run
    while True:
        async with CodexClient(args.socket) as client:
            loaded = await client.loaded_threads()
        if len(loaded) > 1:
            raise RuntimeError("Multiple loaded threads: select one explicitly with bridge.py")
        if loaded:
            args.native_id = loaded[0]
            runtime_file = Path(args.socket).parent / "runtime.json"
            runtime = json.loads(runtime_file.read_text())
            runtime["native_id"] = args.native_id
            runtime_file.write_text(json.dumps(runtime, indent=2) + "\n")
            args.client, args.model, args.once = "codex", "", False
            await run(args)
            return
        await asyncio.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--resource", action="append", default=[])
    parser.add_argument("--socket", help=argparse.SUPPRESS)
    parser.add_argument("codex_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.socket:
        asyncio.run(receive(args))
        return 0
    directory = Path(tempfile.mkdtemp(prefix="tjai-codex-"))
    address = "unix://" + str(directory / "codex.sock")
    env = {**os.environ}
    with open(directory / "runtime.log", "w") as log:
        server = subprocess.Popen(["codex", "app-server", "--listen", address],
            cwd=args.cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(100):
        if server.poll() is not None:
            raise RuntimeError(f"App-server exited; see {directory}/runtime.log")
        if (directory / "codex.sock").exists():
            break
        time.sleep(.1)
    else:
        server.terminate()
        raise RuntimeError(f"App-server did not open its socket; see {directory}/runtime.log")
    receiver_args = [sys.executable, str(Path(__file__).resolve()), "--socket", str(directory / "codex.sock"),
                     "--name", args.name, "--host", args.host, "--cwd", args.cwd]
    for resource in args.resource:
        receiver_args.extend(["--resource", resource])
    with open(directory / "bridge.log", "w") as log:
        receiver = subprocess.Popen(receiver_args, cwd=args.cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    extra = args.codex_args[1:] if args.codex_args[:1] == ["--"] else args.codex_args
    tui = ["codex", "--remote", address, *extra]
    (directory / "runtime.json").write_text(json.dumps({
        "server_pid": server.pid, "receiver_pid": receiver.pid, "address": address,
        "name": args.name, "cwd": args.cwd,
    }, indent=2) + "\n")
    print(f"TJAI Codex runtime: {directory}\nReceiver log: {directory}/bridge.log", flush=True)
    try:
        return subprocess.call(tui, cwd=args.cwd, env=env)
    finally:
        runtime = json.loads((directory / "runtime.json").read_text())
        reconnect = ["codex", "--remote", address]
        if runtime.get("native_id"):
            reconnect.extend(["resume", runtime["native_id"]])
        else:
            reconnect.append("agents")
        print("Runtime and receiver remain running. Reconnect with:\n" + shlex.join(reconnect), flush=True)
        print(f"After all work has finished, stop these runtime processes: {server.pid} {receiver.pid}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
