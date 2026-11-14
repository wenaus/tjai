"""Antigravity hook: TJAI registration at SessionStart, peer mail before each model call.

Installed through tjrepo computers/common/antigravity-hooks.json, which
~/.gemini/config/hooks.json links to; the Antigravity App, CLI and IDE all read
that file. Antigravity runs the command through `sh -c` with the hook payload
as JSON on stdin and reads the hook result as JSON from stdout.

  SessionStart   register the conversation, start its receiver, inject the
                 TJAI session instructions
  PreInvocation  start the receiver if it is not running (conversations opened
                 before this hook existed join here), then inject spooled peer
                 messages as system messages

  antigravity_hook.py --rename NAME --conversation CONVERSATION_ID
                 give the conversation a TJAI name (Antigravity has no session
                 names of its own); the receiver carries it at its next heartbeat
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CLIENT = "antigravity"


def system(text):
    return {"systemMessage": {"systemMessage": text}}


def rename_help(native_id):
    script = Path(__file__).resolve()
    return (f" This Antigravity conversation's id is {native_id}. To take a TJAI name, run "
            f"`python3 {script} --rename NAME --conversation {native_id}`.")


def session_start(data):
    from startup import start
    text = start(CLIENT, data, context_loaded=True)
    return {"injectSteps": [system(text + rename_help(data["conversationId"]))]} if text else {}


def record_accepted(messages, session_id):
    import mcp_call
    for message in messages:
        try:
            mcp_call.call("record_delivery", {"session_id": session_id, "message_id": message["message_id"],
                                              "state": "accepted_by_client"}, timeout=10)
        except SystemExit as exc:
            print(f"TJAI: record_delivery failed for {message['message_id']}: {exc}", file=sys.stderr)


def pre_invocation(data):
    from antigravity_queue import take
    from startup import session_id_for, start
    start(CLIENT, data)            # no-op while this conversation's receiver holds its lock
    messages = take(data["conversationId"])
    if not messages:
        return {}
    record_accepted(messages, session_id_for(CLIENT, data["conversationId"]))
    return {"injectSteps": [system(message["text"]) for message in messages]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rename", metavar="NAME")
    parser.add_argument("--conversation", metavar="CONVERSATION_ID")
    args = parser.parse_args()
    if args.rename:
        from antigravity_queue import name_file
        if not args.conversation:
            parser.error("--rename needs --conversation")
        path = name_file(args.conversation)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(args.rename.strip() + "\n")
        print(f"TJAI name {args.rename.strip()} set for conversation {args.conversation}; "
              "the receiver carries it at its next heartbeat.")
        return 0
    data = json.load(sys.stdin)
    event = data.get("hookEventName") or ""
    if not data.get("conversationId"):
        print(json.dumps({}))
        print("TJAI: hook payload carries no conversationId", file=sys.stderr)
        return 0
    try:
        result = session_start(data) if event == "SessionStart" else pre_invocation(data)
    except Exception as exc:  # A hook failure must not stop the agent; it is reported, not hidden.
        print(f"TJAI {event or 'hook'} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        result = {"injectSteps": [{"errorMessage": {"userMessage":
                  f"TJAI communications hook failed ({type(exc).__name__}: {exc}); see ~/.tjai/comms."}}]}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
