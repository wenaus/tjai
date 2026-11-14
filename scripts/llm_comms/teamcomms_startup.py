"""Interactive TeamComms enrollment policy, shared by Claude and Codex startup."""

import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

CONFIG = Path.home() / ".config/teamcomms/torre-ai"
ENROLLED = Path.home() / ".tjai/teamcomms-enrolled"
# Clients of the operator's own sessions that this policy enrolls. The
# operator's Codex sessions never join; other participants choose their own.
CLIENTS = {"claude"}


def eligible(host, name, client, interactive=True):
    """Every interactive session of an admitted client; the machine's realm decides the rest.

    Scheduled runs (TJAI_ACTION_ID) and non-interactive runs (claude -p) never join.
    host and name no longer restrict enrollment; they stay in the signature for callers.
    """
    return bool(client in CLIENTS and interactive and not os.environ.get("TJAI_ACTION_ID"))


def realm_admits():
    """(allowed, reason) from the machine's Claude realm: TeamComms is the tech realm's alone.

    The realm module ships with the Claude hooks (tjrepo computers/common/claude-hooks,
    linked at ~/.claude/hooks); without it no session enrolls.
    """
    sys.path.append(str(Path.home() / ".claude/hooks"))
    try:
        import claude_realm
    except ImportError as exc:
        return False, f"claude_realm module unavailable: {exc}"
    return claude_realm.admits_teamcomms()


def silence(path):
    """Disable automatic greetings in the existing host configuration."""
    data = json.loads(path.read_text())
    if data.get("greeting") is not False:
        data["greeting"] = False
        path.write_text(json.dumps(data, indent=2) + "\n")


def codex_name(host, native_id, hint):
    """Resolve an explicitly named session from the authenticated TJAI directory."""
    if "codex" not in CLIENTS:
        return ""
    if eligible(host, hint, "codex"):
        return hint
    if os.environ.get("TJAI_ACTION_ID") or host not in {"ec2dev", "swf-testbed"}:
        return ""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import mcp_call

    offset = 0
    while True:
        try:
            rows = json.loads(mcp_call.call("list_sessions", {"host": host, "limit": 50,
                                                            "offset": offset}, timeout=3))
        except SystemExit as exc:
            raise RuntimeError("TJAI session directory is unavailable") from exc
        if not isinstance(rows, list):
            raise ValueError("TJAI session directory did not return a session list")
        for row in rows:
            if row.get("native_id") == native_id and row.get("client") == "codex":
                return row.get("name") or ""
        if len(rows) < 50:
            return ""
        offset += 50


def enrolled_marker(pid, native_id):
    """One marker per Claude process and session: present once that process has enrolled."""
    ENROLLED.mkdir(parents=True, exist_ok=True)
    for old in ENROLLED.iterdir():
        try:
            os.kill(int(old.name.split("-", 1)[0]), 0)
        except (ValueError, ProcessLookupError):
            old.unlink(missing_ok=True)
        except PermissionError:
            pass
    return ENROLLED / f"{pid}-{native_id}"


RENAME = """
import asyncio, json, sys
from teamcomms.connectors.config import load
from teamcomms.connectors.startup import register
d = json.load(sys.stdin)
asyncio.run(asyncio.wait_for(register(load(d["config"]), d["native_id"], d["name"],
                                      d["cwd"], d["model"]), timeout=10))
"""


def model_id(data):
    model = data.get("model") or ""
    return (model.get("id") or model.get("display_name") or "") if isinstance(model, dict) else model


def claude_hook(data):
    """Enroll at SessionStart; at each prompt, carry a /rename into TeamComms.

    Registration is keyed by the native session id, so registering again under
    the new name renames the enrolled session in place.
    """
    from startup import claude_record

    native_id = data.get("session_id")
    record = claude_record(native_id)
    name = (record.get("name") or data.get("session_name")
            or data.get("session_title") or os.environ.get("TJAI_SESSION_NAME") or "")
    if not eligible(None, name, "claude", record.get("kind") == "interactive"):
        return 0
    marker = enrolled_marker(record.get("pid"), native_id)
    if marker.exists():
        text = marker.read_text()
        enrolled = json.loads(text) if text else {"name": name}   # marker from before renames
        if not name or enrolled.get("name") == name:
            return 0
    else:
        enrolled = None
    allowed, reason = realm_admits()
    if not allowed:
        print(f"TeamComms: not enrolling {name or native_id}: {reason}", file=sys.stderr)
        return 0
    python, config = CONFIG / "python", CONFIG / "connector.json"
    if not (os.access(python, os.X_OK) and config.is_file() and (CONFIG / "token").is_file()):
        return 0
    if enrolled is not None:
        payload = {"config": str(config), "native_id": native_id, "name": name,
                   "cwd": enrolled.get("cwd") or data.get("cwd") or os.getcwd(),
                   "model": enrolled.get("model") or ""}
        code = subprocess.run([str(python), "-c", RENAME], input=json.dumps(payload),
                              text=True).returncode
        if code == 0:
            marker.write_text(json.dumps({**enrolled, "name": name}))
        else:
            print(f"TeamComms: renaming {native_id} to {name} failed (exit {code})", file=sys.stderr)
        return code
    silence(config)
    code = subprocess.run([str(python), "-m", "teamcomms.connectors.cli", "--config",
                           str(config), "hook", "claude"],
                          input=json.dumps({**data, "session_name": name}), text=True).returncode
    if code == 0:
        marker.write_text(json.dumps({"name": name, "model": model_id(data),
                                      "cwd": data.get("cwd") or os.getcwd()}))
    return code


def refresh_supervisors():
    """Reload local Codex policy supervisors while preserving native clients."""
    script = Path(__file__).with_name("launch_codex.py").resolve()
    processes = subprocess.check_output(["ps", "-u", str(os.getuid()), "-o", "pid=,args="], text=True)
    refreshed = []
    for row in processes.splitlines():
        pid_text, _, command = row.strip().partition(" ")
        args = shlex.split(command)
        if (len(args) != 4 or args[2] != "--tjai-supervise"
                or Path(args[1]).resolve() != script):
            continue
        directory = Path(args[3])
        runtime = json.loads((directory / "runtime.json").read_text())
        if (directory / "disconnected").exists():
            continue
        os.kill(runtime["server_pid"], 0)
        os.kill(runtime["launcher_pid"], 0)
        pid = int(pid_text)
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                   capture_output=True, text=True)
            if state.returncode == 1 or state.stdout.strip().startswith("Z"):
                break
            state.check_returncode()
            time.sleep(.1)
        else:
            raise RuntimeError(f"Supervisor {pid} did not exit; no replacement started")
        env = {**os.environ, "TJAI_CODEX_SOCKET": str(directory / "codex.sock"),
               "TJAI_COMMS_PYTHON": args[0]}
        with (directory / "supervisor.log").open("a") as log:
            process = subprocess.Popen(args, cwd=runtime["cwd"], env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        os.kill(runtime["server_pid"], 0)
        refreshed.append({"old_supervisor": pid, "supervisor": process.pid,
                          "native_server": runtime["server_pid"]})
    print(json.dumps({"refreshed": refreshed}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disable-greetings", action="store_true")
    parser.add_argument("--refresh-supervisors", action="store_true")
    args = parser.parse_args()
    if args.refresh_supervisors:
        refresh_supervisors()
    elif args.disable_greetings:
        silence(CONFIG / "connector.json")
    else:
        raise SystemExit(claude_hook(json.load(sys.stdin)))
