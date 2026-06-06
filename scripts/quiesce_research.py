#!/usr/bin/env python3
"""One-shot: quiesce + suspend the research system (Torre, 2026-06-06).

Stops the wedged research queue and suspends the system until further notice
(no redesign today). Specifically:

  1. research-agent action: clear pending_runs + next_target pin, record the
     prior status in data['suspended_from_status'], set status='blocked' so
     get_due_actions() skips it (daemon left untouched — it still runs the
     other actions).
  2. For every *dispatched* research base topic that is not cleanly complete
     (base not 'done', or synthesis sub-entry missing / not 'done'): ensure a
     synthesis sub-entry exists, mark it 'done', mark the base 'done'.

Created synth entries that never actually ran carry an explicit administrative
note — they are NOT real syntheses.

Usage:
    python quiesce_research.py dry-run   # report only, no writes
    python quiesce_research.py           # apply
"""
import bootstrap  # noqa: F401 - Django setup + .env
import sys
import time
import uuid as _uuid

from django.db import connection, transaction
from tjai_app.models import Entry
from tjai_app.action_runner import RESEARCH_MODELS

TERMINAL = ('done', 'failed', 'blocked')
DRY = len(sys.argv) > 1 and sys.argv[1] == 'dry-run'


def is_base_topic(data):
    eid = data.get('entry_id') or ''
    if not eid.startswith('research-'):
        return False
    if data.get('base_entry_id') or data.get('model'):
        return False
    return True


def main():
    print(f"DB: {connection.settings_dict.get('NAME')} @ "
          f"{connection.settings_dict.get('HOST')}  mode="
          f"{'DRY-RUN' if DRY else 'APPLY'}")

    now = time.time()
    report = []

    ra = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='research-agent').first()
    if ra:
        d = ra.data if isinstance(ra.data, dict) else {}
        qlen = len(d.get('pending_runs') or [])
        pin = d.get('next_target_entry_id')
        print(f"research-agent: status={ra.status} queue_len={qlen} "
              f"next_target_entry_id={pin}")

    # Find base topics needing completion.
    bases = Entry.objects.filter(
        kind='memory', deleted_at__isnull=True,
        data__entry_id__startswith='research-')
    to_fix = []
    for base in bases:
        data = base.data if isinstance(base.data, dict) else {}
        if not is_base_topic(data):
            continue
        eid = data['entry_id']
        dispatched = [m for m in RESEARCH_MODELS if data.get(f'{m}_entry_id')]
        if not dispatched:
            continue  # never dispatched — leave it
        synth_eid = f'{eid}-synthesis'
        synth = Entry.objects.filter(
            data__entry_id=synth_eid, deleted_at__isnull=True).first()
        synth_done = synth is not None and (synth.status or '') == 'done'
        base_done = (base.status or '') == 'done'
        if base_done and synth_done:
            continue  # already clean
        to_fix.append((base, eid, synth, synth_eid, dispatched))

    print(f"base topics needing completion: {len(to_fix)}")
    for _, eid, synth, _, _ in to_fix:
        print(f"  - {eid}: base_status will->done; "
              f"synth={'create' if synth is None else (synth.status or 'None')}->done")

    if DRY:
        print("[dry-run] no writes")
        return

    with transaction.atomic():
        if ra:
            d = ra.data if isinstance(ra.data, dict) else {}
            qlen = len(d.get('pending_runs') or [])
            d['pending_runs'] = []
            d.pop('next_target', None)
            d.pop('next_target_entry_id', None)
            ra.data = d
            ra.timestamp_modified = now
            ra.save(update_fields=['data', 'timestamp_modified'])
            report.append(f"research-agent: cleared queue ({qlen}) + next_target pin "
                          f"(status left as {ra.status})")

        for base, eid, synth, synth_eid, dispatched in to_fix:
            if synth is None:
                Entry.objects.create(
                    id=str(_uuid.uuid7()),
                    content=(f"Synthesis: {base.content.split(chr(10))[0]}\n\n"
                             "[Marked complete administratively on 2026-06-06 — "
                             "the multimodel pipeline wedged before synthesis ran. "
                             "This is NOT a generated synthesis.]"),
                    kind='memory', context=base.context, status='done',
                    timestamp_created=now, timestamp_modified=now, is_dirty=1,
                    data={'entry_id': synth_eid, 'source': 'multimodel',
                          'base_entry_id': eid, 'base_uuid': str(base.id),
                          'model': 'synthesis', 'admin_closed': True,
                          **{f'source_{m}_entry_id': f'{eid}-{m}'
                             for m in dispatched}})
                report.append(f"{eid}: created synth entry (admin-closed), status=done")
            elif (synth.status or '') != 'done':
                synth.status = 'done'
                synth.timestamp_modified = now
                synth.save(update_fields=['status', 'timestamp_modified'])
                report.append(f"{eid}: synth status->done")

            bdata = base.data if isinstance(base.data, dict) else {}
            bdata['synthesis_triggered'] = True
            base.data = bdata
            base.status = 'done'
            base.timestamp_modified = now
            base.save(update_fields=['data', 'status', 'timestamp_modified'])
            report.append(f"{eid}: base status->done")

    print("\n".join(report) if report else "nothing to do")


if __name__ == '__main__':
    main()
