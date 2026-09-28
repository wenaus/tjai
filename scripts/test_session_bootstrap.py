#!/usr/bin/env python3
"""Focused checks of bootstrap completeness, scope and failure handling."""
import session_bootstrap as boot


def rejects(function, message):
    try:
        function()
    except RuntimeError as exc:
        assert message in str(exc), str(exc)
    else:
        raise AssertionError(f"Expected failure containing {message!r}")


requests = []


def paged(tool, arguments):
    requests.append(arguments)
    offset = arguments["offset"]
    return {"entries": [{"entry_id": str(offset), "content": "complete content"}],
            "complete": offset == 1, "next_offset": None if offset else 1}


assert len(list(boot.pages("get_profile", {}, paged))) == 2
assert [row["offset"] for row in requests] == [0, 1]
rejects(lambda: list(boot.pages("get_profile", {}, lambda *_: {
    "entries": [], "complete": False, "next_offset": 0})), "non-forward")


def shared(tool, arguments):
    if arguments["context"] == "swf":
        assert arguments["location_name"] == "swf-testbed"
        assert arguments.get("include_general", True)
        entries = [{"entry_id": "general", "content": "Full common instruction."}]
    else:
        assert arguments["include_general"] is False and "location_name" not in arguments
        entries = []
    entries.append({"entry_id": arguments["context"], "content": "Full project instruction."})
    return {"entries": entries, "complete": True}


assert len(boot.guidance(("swf", "tjai"), "swf-testbed", shared)) == 3
rejects(lambda: boot.guidance(("swf", "tjai"), "swf-testbed", lambda _, arguments: {
    "entries": [{"entry_id": "same", "content": arguments["context"]}],
    "complete": True}), "Guidance changed")


def activities(tool, arguments):
    assert tool == "get_todos" and arguments["context"] == "swf"
    assert arguments["summary_only"] is True and arguments["status"] == "inflight"
    return [{"id": "uuid", "entry_id": "swf-example", "title": "Activity title",
             "context": "swf", "status": "inflight", "open": 1, "done": 2}]


index = boot.activity_index("swf", activities)
assert "Activity title" in index and "swf-example" in index and "History" not in index
assert "1 open, 2 done" in index


def health(tool, arguments):
    assert tool == "get_entry_by_entry_id"
    assert arguments["heading"] == "Health Assessment" and arguments["level"] == 2
    return {"modified": "2026-09-28", "content": "## Health Assessment\nFull assessment."}


assert "Full assessment." in boot.health_assessment(health)


def oversized(tool, arguments):
    if tool == "get_profile":
        return {"entries": [{"entry_id": "profile", "content": "X" * boot.MAX_OUTPUT_CHARS}],
                "complete": True}
    if tool == "get_ai_guidance":
        return {"entries": [], "complete": True}
    if tool in ("get_todos", "list_sessions"):
        return []
    return {"modified": "2026-09-28", "content": "## Health Assessment\nHealthy."}


rejects(lambda: boot.build("test-host", ("tjai",), oversized), "No content was truncated")

own = {"id": "self", "native_id": "native-self", "host": "test-host", "client": "codex",
       "online": True, "delivery": "codex_app_server", "name": "test-1", "state": "active"}
assert "Self: test-1" in boot.peer_index(lambda *_: [own], native_id="native-self", location="test-host")
for peers in ([], [{**own, "online": False}], [{**own, "delivery": "pull"}]):
    rejects(lambda: boot.peer_index(lambda *_: peers, native_id="native-self", location="test-host"),
            "no online native TJAI receiver")
rejects(lambda: boot.build("test-host", ("tjai",), oversized, native_id="native-self"),
        "TJAI readiness check failed")
print("Bootstrap completeness, deduplication, activity scope, budget and receiver readiness checks passed.")
