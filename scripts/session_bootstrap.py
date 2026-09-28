#!/usr/bin/env python3
"""Read the complete session guidance and a scoped work index through TJAI MCP."""
import argparse
import json
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import mcp_call

WORKSPACE = Path(__file__).resolve().parents[2]
MAX_OUTPUT_CHARS = 52_000
HOST_CONTEXTS = {
    "swf-testbed": ("swf", "tjai"),
    "StudioMax": ("tjai",),
    "MiniPower": ("tjai",),
    "ec2dev": ("tjai",),
    "MacbookPro": ("tjai",),
    "mbp": ("tjai",),
}


def call(tool, arguments):
    try:
        result = json.loads(mcp_call.call(tool, arguments, timeout=15))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{tool}: invalid JSON response") from exc
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(f"{tool}: {result['error']}")
    return result


def pages(tool, arguments, read=call):
    """Follow every startup page and reject a stalled or malformed cursor."""
    offset = 0
    while True:
        page = read(tool, {**arguments, "offset": offset})
        if not isinstance(page, dict) or not isinstance(page.get("entries"), list):
            raise RuntimeError(f"{tool}: missing startup entries")
        yield from page["entries"]
        if page.get("complete") is True:
            return
        following = page.get("next_offset")
        if page.get("complete") is not False or type(following) is not int or following <= offset:
            raise RuntimeError(f"{tool}: non-forward startup cursor at {offset}")
        offset = following


def guidance(contexts, location, read=call):
    """Emit each authoritative entry once, preserving its entire content."""
    entries = {}
    for context in contexts:
        arguments = {"audience": "openai"}
        if context:
            arguments["context"] = context
        if location:
            arguments["location_name"] = location
        for entry in pages("get_ai_guidance", arguments, read):
            key = entry.get("entry_id") or json.dumps(entry, sort_keys=True)
            if key in entries and entries[key] != entry:
                raise RuntimeError(f"Guidance changed during bootstrap: {key}; rerun once")
            entries[key] = entry
    return list(entries.values())


def section(content, title):
    lines = []
    selected = False
    for line in content.splitlines():
        if line == f"## {title}":
            selected = True
            continue
        if selected and line.startswith("## "):
            break
        if selected:
            lines.append(line)
    return "\n".join(lines).strip()


def activity_index(context, read=call):
    arguments = {"status": "inflight", "max_content_length": 0}
    if context:
        arguments["context"] = context
    entries = read("get_todos", arguments)
    if not isinstance(entries, list):
        raise RuntimeError("get_todos: expected an activity list")
    rows = []
    for entry in entries:
        slug = entry.get("data", {}).get("entry_id") or entry["id"]
        title = next((line for line in entry["content"].splitlines() if line.strip()), slug)
        rows.append(f"- {slug}: {title.lstrip('# ')} (updated {entry['modified']})")
    return "\n".join(rows) or "No inflight activities in this context."


def health_assessment(read=call):
    today = datetime.now(ZoneInfo("America/New_York")).date()
    errors = []
    for day in (today, today - timedelta(days=1)):
        slug = f"daily-{day.isoformat()}"
        try:
            entry = read("get_entry_by_entry_id", {"entry_id": slug})
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        assessment = section(entry.get("content", ""), "Health Assessment")
        if assessment:
            return f"Report: {slug}; recorded {entry['modified']}\n\n{assessment}"
        errors.append(f"{slug}: no Health Assessment section")
    raise RuntimeError("Health assessment unavailable: " + "; ".join(errors))


def peer_index(read=call):
    peers = read("list_sessions", {"include_offline": False})
    if not isinstance(peers, list):
        raise RuntimeError("list_sessions: expected a session list")
    return "\n".join(
        f"- {peer['id']}: {peer['name']} on {peer['host']} ({peer['client']}; {peer['state']})"
        for peer in peers
    ) or "No online peers."


def render_entries(title, entries):
    parts = [f"## {title}"]
    for entry in entries:
        identity = entry.get("entry_id", entry.get("kind", "notice"))
        content = entry.get("content", "")
        parts.append(f"### {identity}\n{content}")
    return "\n\n".join(parts)


def build(location, contexts, read=call):
    # Profile is acquired first. Every required guidance page follows.
    profile = list(pages("get_profile", {}, read))
    rules = guidance(contexts, location, read)
    parts = [f"# Session bootstrap — {location}", render_entries("Profile", profile),
             render_entries("Guidance and machine facts", rules)]
    if "swf" in contexts:
        path = WORKSPACE / "CLAUDE.md"
        if not path.is_file():
            raise RuntimeError(f"SWF workspace guidance missing: {path}")
        parts.append(f"## SWF workspace guidance\n\n{path.read_text()}")
    work_context = contexts[0]
    reads = [(f"Inflight index ({work_context or 'general'})", lambda: activity_index(work_context, read)),
             ("Latest daily health assessment", lambda: health_assessment(read)),
             ("Online peers", lambda: peer_index(read))]
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [(title, executor.submit(fetch)) for title, fetch in reads]
        for title, future in futures:
            try:
                content = future.result()
            except (RuntimeError, OSError, SystemExit) as exc:
                content = f"Unavailable: {exc}. Report this gap; fetch it when needed."
            parts.append(f"## {title}\n\n{content}")
    parts.append(
        "## Working protocol\n\n"
        "The profile and guidance above are complete, with shared entries emitted once. "
        "Do not reload them for this boot. The inflight index selects metadata only; "
        "get_entry_by_entry_id reads the assigned activity in full, then read its Refs. "
        "For work in another context, first retrieve that context's activity index. "
        "Read the relevant repo README, CLAUDE.md and task documents before implementation. "
        "On swf-testbed, the PCS/epicprod doc set named in guidance is read when that work begins. "
        "Use the peer index for the once-per-session AI Hi, excluding this session and background jobs. "
        "Use MCP for ongoing TJAI work. Discover only needed tool names/signatures; never print ALL_TOOLS "
        "or a complete catalog. Decode one MCP result representation. "
        "For continuity, fetch the relevant host/session dialog or activity, keeping full content "
        "for selected records. Boot alone does not resume an activity. "
        "Finish with a brief ready message and any concrete health warnings."
    )
    output = "\n\n".join(parts) + "\n"
    if len(output) > MAX_OUTPUT_CHARS:
        raise RuntimeError(f"Bootstrap is {len(output):,} characters, above the {MAX_OUTPUT_CHARS:,} "
                           "budget. No content was truncated; inspect the oversized section.")
    return output, {"characters": len(output), "words": len(output.split()),
                    "profile_entries": len(profile), "guidance_entries": len(rules),
                    "section_characters": {part.splitlines()[0]: len(part) for part in parts}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location")
    parser.add_argument("--measure", action="store_true", help="Fetch and validate; emit only size counts")
    arguments = parser.parse_args()
    location = arguments.location
    if not location:
        config = Path.home() / ".tjai/config.json"
        if config.exists():
            location = json.loads(config.read_text()).get("location_name")
        location = location or socket.gethostname()
    contexts = HOST_CONTEXTS.get(location, (None,))
    output, metrics = build(location, contexts)
    print(json.dumps(metrics, indent=2) if arguments.measure else output, end="\n" if arguments.measure else "")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        sys.exit(f"session_bootstrap.py: {exc}")
