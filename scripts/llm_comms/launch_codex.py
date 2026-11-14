"""Normal interactive Codex launcher with automatic TJAI native delivery.

Each launch owns one private app-server. SessionStart registers each thread;
the supervisor covers clients with hooks disabled and later /new or /resume.
Noninteractive and administrative subcommands pass through untouched.
"""

import asyncio
from datetime import date, datetime, time as toml_time
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import tomllib

from codex_client import CodexClient
from startup import machine, start
from teamcomms_startup import codex_name, eligible, silence


VALUE_OPTIONS = {"-c", "--config", "-C", "--cd", "-m", "--model", "-p", "--profile",
                 "-s", "--sandbox", "-a", "--ask-for-approval", "--enable", "--disable",
                 "--add-dir", "--image", "-i", "--local-provider", "--remote-auth-token-env"}
COMMANDS = {"agents", "exec", "e", "review", "login", "logout", "mcp", "plugin", "app-server",
            "remote-control", "completion", "update", "doctor", "sandbox", "debug", "apply", "a",
            "queue", "archive", "delete", "migrate-rollouts", "unarchive", "cloud", "exec-server",
            "features", "help"}


def toml_value(value):
    """Render a parsed TOML value for Codex's key=value config override syntax."""
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date, toml_time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{json.dumps(str(key))} = {toml_value(item)}" for key, item in value.items()
        ) + " }"
    return str(value)


def profile_server_config(profile):
    """Translate a runtime profile into app-server config overrides.

    Codex loads --profile in the TUI but does not accept it on app-server. The
    external server still needs the profile's provider and runtime settings, so
    pass the resolved profile values through app-server's -c interface.
    """
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    profile_path = codex_home / f"{profile}.config.toml"
    if not profile_path.is_file():
        return []
    with profile_path.open("rb") as handle:
        settings = tomllib.load(handle)

    overrides = []
    for key, value in settings.items():
        overrides.extend(["-c", f"{key}={toml_value(value)}"])
    return overrides


def launch_options(arguments):
    """Inspect routing/cwd only; forward the original argument list verbatim."""
    cwd = os.getcwd()
    server_config = []
    profile = None
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
            if arg in {"-p", "--profile"}:
                profile = value
            if arg in {"-c", "--config", "--enable", "--disable"}:
                server_config.extend([arg, value])
            i += 2
            continue
        if arg.startswith("--cd="):
            cwd = os.path.abspath(os.path.expanduser(arg.split("=", 1)[1]))
        elif arg.startswith("--profile="):
            profile = arg.split("=", 1)[1]
        elif arg.startswith(("--config=", "--enable=", "--disable=")):
            server_config.append(arg)
        elif not arg.startswith("-"):
            if arg in COMMANDS:
                return None
            break  # Interactive prompt, resume or fork: other arguments stay native.
        i += 1
    if profile is not None:
        server_config = profile_server_config(profile) + server_config
    return cwd, server_config


def read_runtime(directory):
    return json.loads((directory / "runtime.json").read_text())


TEAMCOMMS = Path.home() / ".config/teamcomms/torre-ai"


def start_teamcomms(directory, data):
    """Attach a SWF TeamComms receiver to this owning app-server on an enrolled host."""
    host, _ = machine()
    python, config = TEAMCOMMS / "python", TEAMCOMMS / "connector.json"
    if not (os.access(python, os.X_OK) and config.is_file() and (TEAMCOMMS / "token").is_file()):
        return False
    name = codex_name(host, data["session_id"], data.get("session_name"))
    if not eligible(host, name, "codex"):
        return False
    silence(config)
    with open(directory / f"teamcomms-{data['session_id']}.log", "a") as log:
        subprocess.Popen([str(python), "-m", "teamcomms.connectors.cli", "--config", str(config), "receive",
            "--client", "codex", "--native-id", data["session_id"], "--socket", str(directory / "codex.sock"),
            "--pid", str(data["owner_pid"]), "--name", name, "--cwd", data["cwd"],
            "--model", data["model"]], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return True


async def supervise(directory):
    runtime = read_runtime(directory)
    host, _ = machine()
    watched = set()
    teamcomms_watched, teamcomms_attempted = set(), {}
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
                    data = {"session_id": native_id, "cwd": thread.get("cwd", runtime["cwd"]),
                                "session_name": thread.get("name") or f"{host}-codex-{native_id[-8:]}",
                                "model": thread.get("model") or "",
                            "owner_pid": runtime["server_pid"]}
                    if native_id not in watched:
                        start("codex", data, host)
                        watched.add(native_id)
                    if (native_id not in teamcomms_watched
                            and time.monotonic() - teamcomms_attempted.get(native_id, 0) >= 30):
                        teamcomms_attempted[native_id] = time.monotonic()
                        if start_teamcomms(directory, data):
                            teamcomms_watched.add(native_id)
                watched.intersection_update(loaded)
                teamcomms_watched.intersection_update(loaded)
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
