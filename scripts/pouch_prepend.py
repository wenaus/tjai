#!/usr/bin/env python3
"""Put a draft at the top of the Pouch (capcom-pouch), above what it holds.

Usage: pouch_prepend.py <markdown file>

The Pouch's current content stays below the new draft, unchanged; the
previous version stays in the entry's history. Prints the tool's diff.
Writing to the Pouch needs Torre's word (ai-pouch-exchange).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_call import call  # noqa: E402


def parsed(text):
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get('result'), str):
        data = json.loads(data['result'])
    if isinstance(data, dict) and data.get('error'):
        sys.exit(f"Pouch tool error: {data['error']}")
    return data


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    draft = open(sys.argv[1]).read().strip()
    if not draft:
        sys.exit('empty draft')
    entry = parsed(call('get_entry_by_entry_id', {'entry_id': 'capcom-pouch'}))
    entry = entry.get('entry', entry)
    current = entry.get('content') or ''
    if draft in current:
        sys.exit('the draft is already in the Pouch')
    content = draft + '\n\n' + current if current else draft
    result = parsed(call('replace_entry_content',
                         {'entry_id': entry['id'], 'content': content}))
    print(result.get('diff', '(no diff returned)'))


if __name__ == '__main__':
    main()
