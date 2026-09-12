"""Normal interactive Codex launcher with automatic TJAI native delivery.

Each launch owns one private app-server. SessionStart registers each thread;
the supervisor covers clients with hooks disabled and later /new or /resume.
Noninteractive and administrative subcommands pass through untouched.
"""

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from codex_client import CodexClient
from startup import machine, start


VALUE_OPTIONS = {"-c", "--config", "-C", "--cd", "-m", "--model", "-p", "--profile",
                 "-s", "--sandbox", "-a", "--ask-for-approval", "--enable", "--disable",
                 "--add-dir", "--image", "-i", "--local-provider", "--remote-auth-token-env"}
COMMANDS = {"agents", "exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server",
            "remote-control", "completion", "update", "doctor", "sandbox", "debug", "apply", "a",
            "queue", "archive", "delete", "migrate-rollouts", "unarchive", "cloud", "exec-server",
            "features", "help"}


def launch_options(arguments):
    """Inspect routing/cwd only; forward the original argument list verbatim."""
    cwd = os.getcwd()
    server_config = []
    i = 0
    while i < len(arguments):
        arg = arguments[i]
        if arg in {"-h", "--help", "-V", "--version", "--remote"} or arg.startswith("--remote="):
            return None
        if arg in VALUE_OPTIONS:
            if i + 1 >= len(arguments):
                return None  # Native CLI reports the malformed argument.
            value = arguments[i + 1]
            if arg in {"-C", "--cd"}:
                cwd = os.path.abspath(os.path.expanduser(value))
            if arg in {"-c", "--config", "--enable", "--disable"}:
                server_config.extend([arg, value])
            i += 2
            continue
        if arg.startswith("--cd="):
            cwd = os.path.abspath(os.path.expanduser(arg.split("=", 1)[1]))
        elif arg.startswith(("--config=", "--enable=", "--disable=")):
            server_config.append(arg)
        elif not arg.startswith("-"):
            if arg in COMMANDS:
                return None
            break  # Interactive prompt, resume or fork: other arguments stay native.
        i += 1
    return cwd, server_config


def read_runtime(directory):
    return json.loads((directory / "runtime.json").read_text())


async def supervise(directory):
    runtime = read_runtime(directory)
    host, _ = machine()
    watched = set()
    while True:
        try:
            os.kill(runtime["server_pid"], 0)
            async with CodexClient(str(directory / "codex.sock")) as client:
                loaded = await client.loaded_threads()
                active = False
                for native_id in loaded:
                    response = await client.call("thread/read", {"threadId": native_id, "includeTurns": False})
                    thread = response["thread"]
                    if thread.get("threadSource") != "user":
                        continue  # Internal title/housekeeping models are not operator sessions.
                    active |= thread["status"]["type"] == "active"
                    if native_id not in watched:
                        data = {"session_id": native_id, "cwd": thread.get("cwd", runtime["cwd"]),
                                "session_name": thread.get("name") or f"{host}-codex-{native_id[-8:]}",
                                "model": thread.get("model") or "",
                                "owner_pid": runtime["server_pid"]}
                        start("codex", data, host)
                        watched.add(native_id)
                watched.intersection_update(loaded)
            # TUI exit keeps an already-running turn alive until completion.
            disconnected = (directory / "disconnected").exists()
            try:
                os.kill(runtime["launcher_pid"], 0)
            except ProcessLookupError:
                disconnected = True
            if disconnected and not active:
                os.killpg(runtime["server_pid"], signal.SIGTERM)
                return
        except (FileNotFoundError, ProcessLookupError, ConnectionError):
            return
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"TJAI runtime supervisor: {exc}", flush=True)
        await asyncio.sleep(2)


def main():
    arguments = sys.argv[1:]
    if arguments[:1] == ["--tjai-supervise"]:
        asyncio.run(supervise(Path(arguments[1])))
        return 0
    options = launch_options(arguments)
    if options is None or not sys.stdin.isatty():
        os.execvp("codex", ["codex", *arguments])
    cwd, server_config = options
    directory = Path(tempfile.mkdtemp(prefix="tjai-codex-"))
    address = "unix://" + str(directory / "codex.sock")
    env = {**os.environ, "TJAI_CODEX_SOCKET": str(directory / "codex.sock"), "TJAI_COMMS_PYTHON": sys.executable}
    with open(directory / "runtime.log", "w") as log:
        server = subprocess.Popen(["codex", *server_config, "app-server", "--listen", address],
            cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(100):
        if server.poll() is not None:
            raise RuntimeError(f"App-server exited; see {directory}/runtime.log")
        if (directory / "codex.sock").exists():
            break
        time.sleep(.1)
    else:
        os.killpg(server.pid, signal.SIGTERM)
        raise RuntimeError(f"App-server did not open its socket; see {directory}/runtime.log")
    (directory / "runtime.json").write_text(json.dumps({"server_pid": server.pid, "launcher_pid": os.getpid(), "address": address, "cwd": cwd}) + "\n")
    with open(directory / "supervisor.log", "w") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--tjai-supervise", str(directory)],
            cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    if os.environ.get("TJAI_COMMS_DEBUG"):
        print(f"TJAI communications runtime: {directory}", flush=True)
    try:
        return subprocess.call(["codex", "--remote", address, *arguments], cwd=cwd, env=env)
    finally:
        (directory / "disconnected").touch()
        print("Any running turn will finish before this runtime closes.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
