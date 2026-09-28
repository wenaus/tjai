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
    return {"entries": [{"entry_id": "general", "content": "Full common instruction."},
                        {"entry_id": arguments["context"], "content": "Full project instruction."}],
            "complete": True}


assert len(boot.guidance(("swf", "tjai"), "swf-testbed", shared)) == 3
rejects(lambda: boot.guidance(("swf", "tjai"), "swf-testbed", lambda _, arguments: {
    "entries": [{"entry_id": "same", "content": arguments["context"]}],
    "complete": True}), "Guidance changed")


def activities(tool, arguments):
    assert tool == "get_todos" and arguments["context"] == "swf"
    assert arguments["max_content_length"] == 0 and arguments["status"] == "inflight"
    return [{"id": "uuid", "data": {"entry_id": "swf-example"}, "modified": "2026-09-28",
             "content": "Activity title\n\n## Live\n- A task\n\n## Done\n" + "History\n" * 10_000}]


index = boot.activity_index("swf", activities)
assert "Activity title" in index and "swf-example" in index and "History" not in index
assert boot.section("## Health Assessment\nFull assessment.\n\n## Other\nUnrelated.",
                    "Health Assessment") == "Full assessment."


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
print("Bootstrap completeness, deduplication, activity scope and budget checks passed.")
