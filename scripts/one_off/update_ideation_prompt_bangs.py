#!/usr/bin/env python3
"""Retarget the ideation prompt from @Underway to !!! items and todos.

@Underway stopped being maintained, so the prompt's "compare yesterday's
version" step produced a null diff every night and the agent reported the
absence of change as a finding. This replaces that material-gathering step
with the live-priority sources that took its place — bang lines via
get_todo_bangs() and todo entries — and states that an unchanged item is
not a finding.

Exact-match replacements, asserted: a miss aborts without writing.

Usage:
    python update_ideation_prompt_bangs.py [--apply]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/

import bootstrap  # noqa: E402,F401 - Django setup

from tjai_app import services
from tjai_app.models import Entry

ENTRY_ID = 'ideation-agent'

OLD_SOURCE = """5. **Underway**: get_named_entries(name="Underway") then get_entry_versions(entry_id=<uuid>, age="24h") for yesterday's version. Compare to see what shifted."""

NEW_SOURCE = """5. **Live priorities — `!!!` items and todos**: what the reader has flagged as live work. This replaces the retired `@Underway` entry.

   - get_todo_bangs() — every `!!!` line across the knowledge base. A bang line is a todo written in flow, wherever the thought occurred (a diary entry, a project note, a design document), so it carries the reader's own emphasis and is the strongest available signal of what he considers live. The same set is on the `!!!` page at /tjai/todo-bangs/. Read all of them.
   - search_entries(kind="todo", start_date="48h", max_content_length=0) — todos created or modified in the window: what entered, changed, or advanced.
   - get_todos(max_content_length=0) — the standing open list, for where the new and changed items sit.

   **Stability is not a finding.** A bang line or todo that persists unchanged is a standing priority, not news; never report an absence of change as an observation. Signal lives in what is new, what changed, and what disappeared — completion is done by editing the bangs out of the source line, so a bang line that is gone was finished, which is worth noting as work done."""

OLD_MATERIAL = """    [bulleted summary of picks (with keeps/readmes flagged), dialog,
    activity, diary, Underway diff — specific, full, not brief]"""

NEW_MATERIAL = """    [bulleted summary of picks (with keeps/readmes flagged), dialog,
    activity, diary, and the live-priority picture — new and changed !!!
    items and todos — specific, full, not brief]"""


def main():
    apply = '--apply' in sys.argv
    entry = Entry.objects.filter(
        data__entry_id=ENTRY_ID, deleted_at__isnull=True,
    ).first()
    if not entry:
        sys.exit(f'ERROR: {ENTRY_ID} not found')

    prompt = (entry.data or {}).get('ai_prompt')
    if not prompt:
        sys.exit(f'ERROR: {ENTRY_ID} has no ai_prompt')

    for label, old in (('source-5', OLD_SOURCE), ('material', OLD_MATERIAL)):
        n = prompt.count(old)
        if n != 1:
            sys.exit(f'ERROR: {label} matched {n} times, expected exactly 1 '
                     '— aborting without writing')

    new_prompt = prompt.replace(OLD_SOURCE, NEW_SOURCE)
    new_prompt = new_prompt.replace(OLD_MATERIAL, NEW_MATERIAL)

    if 'Underway' in new_prompt:
        leftover = [ln for ln in new_prompt.split('\n') if 'Underway' in ln]
        print('NOTE: remaining Underway mentions (expected: the one in the '
              'replacement text naming what it replaced):')
        for ln in leftover:
            print('   ', ln.strip()[:110])

    print(f'\nprompt {len(prompt)} -> {len(new_prompt)} chars')
    if not apply:
        print('\ndry run — rerun with --apply to write')
        return

    result = services.edit_entry_metadata(
        entry_id=str(entry.id), data={'ai_prompt': new_prompt},
    )
    if isinstance(result, dict) and 'error' in result:
        sys.exit(f'ERROR: {result["error"]}')
    print('written')


if __name__ == '__main__':
    main()
