#!/usr/bin/env python3
"""Correct dialog records that carry the wrong speaker or a stolen research attribution.

Three faults left records under the wrong name until 2026-10-10:
  - Claude Code peer deliveries went unrecognized (client spelled claude-code by the hook, claude by
    the comms registry), so each was stored a second time as a user turn, read as Torre's;
  - task notifications and a client's own agent prompts were stored as user turns;
  - every session's task notification was attributed to whatever research topic was running.

The script previews by default and writes only with --apply. Every change keeps what it replaced in the
record's data (recorded_role, misattributed_research), so it can be reversed.

Run it from the served tree (/var/www/tjai), whose BASE_DIR is where the research agent runs:

    /var/www/tjai/.venv/bin/python /var/www/tjai/scripts/fix_dialog_attribution.py            # preview
    /var/www/tjai/.venv/bin/python /var/www/tjai/scripts/fix_dialog_attribution.py --apply
"""
import argparse
import re

import bootstrap  # noqa: F401 - Django setup

from django.db import transaction

from tjai_app.comms_dialog import recorded_native_peer
from tjai_app.dialog import HARNESS_PREFIXES, is_research_agent_turn
from tjai_app.dialog_context import DIALOG_TAG
from tjai_app.models import Entry, Tag

PEER = re.compile(r"(?:Another Claude session sent a message:\s*)?\[TJAI peer [0-9a-f-]{36}\]\n")
RESEARCH_KEYS = ('entry_id', 'source_entry_id', 'source_uuid')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    dialog_ids = Tag.objects.filter(tag_name=DIALOG_TAG).values_list('entry_id', flat=True)
    users = Entry.objects.filter(id__in=dialog_ids, deleted_at__isnull=True, data__role='user')

    echoes, unmatched, harness = [], 0, []
    for e in users.filter(content__regex=r'^(Another Claude session sent a message:\s*)?\[TJAI peer ').iterator():
        data = e.data if isinstance(e.data, dict) else {}
        if not PEER.match(e.content):
            continue
        peer = recorded_native_peer(e.content, data, e.timestamp_created) if args.apply else None
        if args.apply and not peer:
            unmatched += 1
            continue
        echoes.append((e, peer))
    for prefix in HARNESS_PREFIXES:
        harness.extend(users.filter(content__startswith=prefix).iterator())

    research_ids = Tag.objects.filter(tag_name='research-subagent').values_list('entry_id', flat=True)
    stolen = [
        e for e in Entry.objects.filter(id__in=research_ids, deleted_at__isnull=True).iterator()
        if isinstance(e.data, dict) and e.data.get('project_path') and not is_research_agent_turn(e.data)
    ]

    print(f"peer echoes: {len(echoes)}" + (f" (unmatched, left as they are: {unmatched})" if args.apply else ""))
    print(f"harness turns stored as user: {len(harness)}")
    print(f"task notifications filed under a research topic they did not come from: {len(stolen)}")
    if not args.apply:
        print("preview only; rerun with --apply to write")
        return

    with transaction.atomic():
        for e, peer in echoes:
            e.data = {**e.data, 'role': 'peer_echo', 'recorded_role': 'user', 'peer_entry_id': str(peer.id)}
            e.save(update_fields=['data'])
        for e in harness:
            e.data = {**e.data, 'role': 'harness', 'recorded_role': 'user'}
            e.save(update_fields=['data'])
        for e in stolen:
            data = dict(e.data)
            data['misattributed_research'] = {k: data.pop(k) for k in RESEARCH_KEYS if k in data}
            e.data = data
            e.save(update_fields=['data'])
            Tag.objects.filter(tag_name='research-subagent', entry=e).delete()
    print("applied")


if __name__ == '__main__':
    main()
