#!/usr/bin/env python3
"""Delete the activities closed as done before closing retired them (docs/inflight.md § Closing).

Inflight is the transient record of current work: an activity set done is deleted in the same write. This takes out
the ones closed before that rule, each through the tjai MCP's `delete_entry` (a soft delete with a version snapshot),
from its current content: every done todo marked as an activity (`data.activity`), and any done todo named on the
command line by its entry_id (one closed without the mark).

Usage: retire_done_activities.py [--dry-run] [entry_id ...]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_call import call  # noqa: E402
from strip_inflight_done import decoded  # noqa: E402


def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    named = {a for a in args if not a.startswith('--')}
    todos = decoded(call('get_todos', {'status': 'done', 'max_content_length': 0}))
    if not isinstance(todos, list):
        sys.exit(f'get_todos: expected a list, got {str(todos)[:200]}')
    retired = failed = 0
    for todo in todos:
        data = todo.get('data') or {}
        name = data.get('entry_id') or todo['id']
        if not (data.get('activity') or name in named):
            continue
        if dry:
            print(f'would retire {name}')
            retired += 1
            continue
        answer = decoded(call('delete_entry', {'entry_id': todo['id'], 'content': todo.get('content') or ''}))
        if not (isinstance(answer, dict) and answer.get('deleted')):
            print(f'NOT retired {name}: {str(answer)[:300]}', file=sys.stderr)
            failed += 1
            continue
        print(f'retired {name}')
        retired += 1
    missing = named - {(t.get('data') or {}).get('entry_id') for t in todos}
    for name in sorted(missing):
        print(f'NOT retired {name}: no done todo by that entry_id', file=sys.stderr)
    print(f'{"would retire" if dry else "retired"} {retired}; {failed + len(missing)} refused')
    sys.exit(1 if failed or missing else 0)


if __name__ == '__main__':
    main()
