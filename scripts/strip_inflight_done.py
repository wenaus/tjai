#!/usr/bin/env python3
"""Take the Done section out of every inflight activity (docs/inflight.md § Shape).

Finished items are not kept in an activity: every session that takes one up reads its body whole, and what is done
is recorded in git and the project's documents. Each activity's `## Done` section, from its heading to the next
heading, is removed with one surgical `replace_text_in_entry` through the tjai MCP (scripts/mcp_call.py), guarded by
the entry's modified time so a concurrent edit is never overwritten; the version history keeps what was removed.

Usage: strip_inflight_done.py [--dry-run]
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_call import call  # noqa: E402

HEADING = re.compile(r'^##\s+(.+?)\s*$')


def decoded(text):
    """A tool's text result as JSON, unwrapping the server's {"result": "<json>"} envelope."""
    value = json.loads(text)
    if isinstance(value, dict) and isinstance(value.get('result'), str):
        value = json.loads(value['result'])
    return value


def done_block(content):
    """The exact text of the Done section, its heading through the line before the next heading, or None."""
    lines = content.split('\n')
    start = next((i for i, line in enumerate(lines)
                  if (m := HEADING.match(line)) and m.group(1) == 'Done'), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if HEADING.match(lines[i])), len(lines))
    if end < len(lines):
        return '\n'.join(lines[start:end]) + '\n'
    return '\n' + '\n'.join(lines[start:end])


def main():
    dry = '--dry-run' in sys.argv[1:]
    todos = decoded(call('get_todos', {'status': 'inflight', 'max_content_length': 0}))
    if not isinstance(todos, list):
        sys.exit(f'get_todos: expected a list, got {str(todos)[:200]}')
    stripped = failed = 0
    for todo in todos:
        name = (todo.get('data') or {}).get('entry_id') or todo['id']
        block = done_block(todo.get('content') or '')
        if block is None:
            continue
        items = sum(1 for line in block.split('\n') if line.startswith('. '))
        if dry:
            print(f'would strip {name}: {items} done items')
            stripped += 1
            continue
        answer = decoded(call('replace_text_in_entry', {
            'entry_id': todo['id'], 'old_text': block, 'new_text': '',
            'expected_modified_at': todo.get('modified')}))
        if isinstance(answer, dict) and answer.get('error'):
            print(f'NOT stripped {name}: {answer.get("code")} {answer["error"]}', file=sys.stderr)
            failed += 1
            continue
        print(f'stripped {name}: {items} done items')
        stripped += 1
    print(f'{"would strip" if dry else "stripped"} {stripped} of {len(todos)} inflight activities; {failed} refused')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
