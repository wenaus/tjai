"""Interactive TeamComms enrollment policy, shared by Claude and Codex startup."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

CONFIG = Path.home() / ".config/teamcomms/torre-ai"


def eligible(host, name):
    """Only named interactive SWF/ec2 sessions join the collaboration directory."""
    prefix = {"ec2dev": "ec2", "swf-testbed": "swf"}.get(host)
    return bool(not os.environ.get("TJAI_ACTION_ID") and prefix
                and re.fullmatch(rf"{prefix}-[1-9][0-9]*", name or ""))


def silence(path):
    """Disable automatic greetings in the existing host configuration."""
    data = json.loads(path.read_text())
    if data.get("greeting") is not False:
        data["greeting"] = False
        path.write_text(json.dumps(data, indent=2) + "\n")


def codex_name(host, native_id, hint):
    """Resolve an explicitly named session from the authenticated TJAI directory."""
    if eligible(host, hint):
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


def claude_hook(data):
    from startup import claude_record, machine

    host, _ = machine()
    record = claude_record(data.get("session_id"))
    name = (record.get("name") or data.get("session_name")
            or data.get("session_title") or os.environ.get("TJAI_SESSION_NAME") or "")
    if not eligible(host, name):
        return 0
    python, config = CONFIG / "python", CONFIG / "connector.json"
    if not (os.access(python, os.X_OK) and config.is_file() and (CONFIG / "token").is_file()):
        return 0
    silence(config)
    return subprocess.run([str(python), "-m", "teamcomms.connectors.cli", "--config",
                           str(config), "hook", "claude"],
                          input=json.dumps({**data, "session_name": name}), text=True).returncode


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disable-greetings", action="store_true")
    args = parser.parse_args()
    if args.disable_greetings:
        silence(CONFIG / "connector.json")
    else:
        raise SystemExit(claude_hook(json.load(sys.stdin)))
