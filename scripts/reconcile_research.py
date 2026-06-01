#!/usr/bin/env python3
"""Reconcile wedged research base topics — the out-of-band sweep.

The research base -> synthesis -> done advancement fires only as a side-effect
of model-completion / agent_complete events. When an event is missed (a rerun
clears synthesis_triggered and resets the base to active; a model fails on an
early-return path; a stale next_target defers the drain), a base lands in
'active' with all its models terminal and nothing ever re-examines it. It then
sits forever until a human clears it by hand.

This sweep ignores how the event chain fired and looks only at end state. For
any base whose dispatched models are all terminal it advances it:
  - no synthesis sub-entry yet  -> dispatch synthesis (mirrors research_model_complete)
  - synthesis sub-entry done     -> flip base to 'done'   (mirrors agent_complete)
  - synthesis sub-entry failed   -> flip base to 'failed' (mirrors agent_complete)
  - synthesis still in progress  -> leave alone
It also clears a research-agent next_target that is pinned on a terminal/gone
topic, which otherwise defers the queue drain indefinitely.

It deliberately does NOT touch bases that were never dispatched (no model
entry_ids) — those are a separate creation-side bug, not a missed transition.

Idempotent; safe to run every few minutes. Standalone (cron), not wired into
the action scheduler on purpose: a reconciler for a flaky scheduler must not
depend on that scheduler to run.

Usage:
    python reconcile_research.py            # reconcile (writes)
    python reconcile_research.py dry-run    # report only, no writes
"""
import bootstrap  # noqa: F401 - Django setup + .env
import logging
import sys

from tjai_app.db_log_handler import DbLogHandler
from tjai_app.models import Entry
from tjai_app.action_runner import RESEARCH_MODELS
from tjai_app import research_queue

logger = logging.getLogger('reconcile_research')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='reconcile_research')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)

TERMINAL = ('done', 'failed', 'blocked')


def _is_base_topic(data):
    """A base research topic: kind=memory, entry_id research-*, and NOT a
    model sub-entry (has base_entry_id) or a synthesis/subagent entry (model)."""
    eid = data.get('entry_id') or ''
    if not eid.startswith('research-'):
        return False
    if data.get('base_entry_id') or data.get('model'):
        return False
    return True


def reconcile_orphan_bases(dry_run=False):
    """Advance any base whose dispatched models are all terminal."""
    actions = []
    bases = Entry.objects.filter(
        kind='memory', status='active', deleted_at__isnull=True,
    )
    for base in bases:
        data = base.data if isinstance(base.data, dict) else {}
        if not _is_base_topic(data):
            continue
        eid = data['entry_id']

        # A model was dispatched iff its entry_id was recorded on the base.
        dispatched = [m for m in RESEARCH_MODELS if data.get(f'{m}_entry_id')]
        if not dispatched:
            continue  # never dispatched — separate creation-side bug, leave it

        statuses = {m: data.get(f'{m}_status') for m in dispatched}
        if not all((s or '') in TERMINAL for s in statuses.values()):
            continue  # genuinely in flight

        # All models terminal — this base should have advanced past 'active'.
        synth_eid = f'{eid}-synthesis'
        synth = Entry.objects.filter(
            data__entry_id=synth_eid, deleted_at__isnull=True,
        ).first()

        if synth is None:
            act = f'{eid}: all models terminal, no synthesis -> dispatch synthesis'
            actions.append(act)
            logger.info(act)
            if not dry_run:
                research_queue.enqueue('synthesize', str(base.id), eid)
                research_queue.drain_if_idle()
        else:
            sstatus = (synth.status or '').lower()
            if sstatus in ('done', 'completed'):
                act = f'{eid}: synthesis done but base still active -> flip base done'
                actions.append(act)
                logger.info(act)
                if not dry_run:
                    base.status = 'done'
                    base.save(update_fields=['status'])
            elif sstatus in ('failed', 'blocked'):
                act = f'{eid}: synthesis failed but base still active -> flip base failed'
                actions.append(act)
                logger.info(act)
                if not dry_run:
                    base.status = 'failed'
                    base.save(update_fields=['status'])
            # else: synthesis in progress (active/None) — leave alone
    return actions


def reconcile_stale_next_target(dry_run=False):
    """Clear a research-agent next_target pinned on a terminal/gone topic.

    Such a pin defers drain_after_complete forever. Only clear when the agent
    is idle and the target is provably terminal or deleted — never disturb a
    target that is legitimately pending pickup."""
    actions = []
    ra = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='research-agent',
    ).first()
    if not ra:
        return actions
    data = ra.data if isinstance(ra.data, dict) else {}
    nt = data.get('next_target_entry_id')
    if not nt:
        return actions
    if research_queue.is_running():
        return actions  # agent may be consuming it right now

    target = Entry.objects.filter(id=nt, deleted_at__isnull=True).first()
    if target is None:
        reason = 'target entry missing'
    elif (target.status or '').lower() in ('done', 'failed'):
        reason = f'target status={target.status}'
    else:
        return actions  # legitimate pending target — leave it

    act = f'research-agent: stale next_target {nt} ({reason}) -> clear + drain'
    actions.append(act)
    logger.info(act)
    if not dry_run:
        data.pop('next_target_entry_id', None)
        data.pop('next_target', None)
        ra.data = data
        ra.save(update_fields=['data'])
        research_queue.drain_if_idle()
    return actions


def main():
    dry_run = len(sys.argv) > 1 and sys.argv[1] == 'dry-run'
    # Clear stale targets first so a freshly-dispatched synthesis below isn't
    # immediately re-examined, and so the drain isn't deferred by a dead pin.
    cleared = reconcile_stale_next_target(dry_run=dry_run)
    healed = reconcile_orphan_bases(dry_run=dry_run)

    actions = cleared + healed
    prefix = '[dry-run] ' if dry_run else ''
    if actions:
        print(f'{prefix}reconciled {len(actions)} item(s):')
        for a in actions:
            print(f'  - {a}')
    else:
        print(f'{prefix}nothing to reconcile')


if __name__ == '__main__':
    main()
