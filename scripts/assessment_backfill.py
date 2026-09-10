#!/usr/bin/env python3
"""Fill in missing daily assessments as wrangler workers (docs/wrangler.md).

Walks back from a start date and enqueues one mechanical worker per day that
has dialog and no assessment. A day that already has an
assessment is never enqueued, so this cannot re-score. Days an earlier
assessor scored are left alone too, because the dashboard plots every assessor
it knows and a second entry for one day would put two points on that date;
--missing-assessor fills those days deliberately.

The workers are ordinary `llm-assessment-gemini` runs carrying their target
date, one durable row each, serialized by the wrangler's per-action key. There
is no chain: a failed day stops nothing, and the queue is the record of what
is left.

    assessment_backfill.py 2026-08-20            # print the plan, enqueue nothing
    assessment_backfill.py 2026-08-20 --enqueue  # queue the missing days
    assessment_backfill.py 2026-08-20 --days 60 --enqueue

Replaces the polled `assessment_gemini_backfill_all` sysconfig flag.
"""
import argparse
import sys
from datetime import datetime, timedelta

import bootstrap  # noqa: F401 - Django setup

from django.utils.timezone import make_aware

from tjai_app.dialog_context import DIALOG_TAG
from tjai_app.models import Entry, Tag
from tjai_app.services import get_timezone
from tjai_app.wrangler import enqueue_action

ACTION_ENTRY_ID = 'llm-assessment-gemini'
MAX_WALK = 90


def assessor_suffix():
    """The current assessor's name, the assessment entry id's suffix."""
    from assessment_gemini import assessor_name
    return assessor_name()


def has_dialog(day, tz):
    """True if any dialog turn was recorded on this day."""
    start = make_aware(datetime.combine(day, datetime.min.time()), tz).timestamp()
    end = make_aware(datetime.combine(day + timedelta(days=1),
                                      datetime.min.time()), tz).timestamp()
    dialog_ids = Tag.objects.filter(tag_name=DIALOG_TAG).values_list('entry_id', flat=True)
    return Entry.objects.filter(id__in=dialog_ids, deleted_at__isnull=True,
                                timestamp_created__gte=start,
                                timestamp_created__lt=end).exists()


def already_scored(day, suffix, any_assessor=True):
    """True if this day already has an assessment.

    By default any assessor's entry counts, because the dashboard plots every
    assessor it knows and a second entry for one day would put two points on
    that date. With any_assessor False only the current assessor's entry
    counts, which fills days an earlier assessor scored.
    """
    prefix = f'assessment-{day.isoformat()}-'
    q = Entry.objects.filter(deleted_at__isnull=True)
    q = (q.filter(data__entry_id__startswith=prefix) if any_assessor
         else q.filter(data__entry_id=f'{prefix}{suffix}'))
    return q.exists()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('start_date', help='most recent day to consider (YYYY-MM-DD)')
    ap.add_argument('--days', type=int, default=30,
                    help=f'days to walk back, max {MAX_WALK} (default 30)')
    ap.add_argument('--enqueue', action='store_true',
                    help='queue the workers; without it the plan is printed only')
    ap.add_argument('--missing-assessor', action='store_true',
                    help="fill days scored only by an earlier assessor too "
                         "(default: any assessment leaves the day alone)")
    args = ap.parse_args()

    try:
        start = datetime.strptime(args.start_date, '%Y-%m-%d').date()
    except ValueError:
        print(f"error: cannot parse date {args.start_date!r} (want YYYY-MM-DD)")
        return 1
    if not 1 <= args.days <= MAX_WALK:
        print(f"error: --days must be 1..{MAX_WALK}")
        return 1

    action = Entry.objects.filter(kind='action', deleted_at__isnull=True,
                                  data__entry_id=ACTION_ENTRY_ID).first()
    if not action:
        print(f"error: action {ACTION_ENTRY_ID} not found")
        return 1
    if (action.data or {}).get('runner') != 'wrangler':
        print(f"error: {ACTION_ENTRY_ID} is not wrangler-owned; "
              "the action agent still owns it and this tool would queue "
              "workers nothing claims")
        return 1

    suffix = assessor_suffix()
    tz = get_timezone()
    missing, scored, quiet = [], [], []
    for n in range(args.days):
        day = start - timedelta(days=n)
        if not has_dialog(day, tz):
            quiet.append(day)
        elif already_scored(day, suffix, any_assessor=not args.missing_assessor):
            scored.append(day)
        else:
            missing.append(day)

    span = f"{(start - timedelta(days=args.days - 1)).isoformat()}..{start.isoformat()}"
    scope = ('missing the current assessor' if args.missing_assessor
             else 'with no assessment at all')
    print(f"assessor {suffix}, {args.days} days ({span}), filling days {scope}")
    print(f"  {len(scored):3} already scored, left alone")
    print(f"  {len(quiet):3} no dialog, skipped")
    print(f"  {len(missing):3} missing -> {'enqueueing' if args.enqueue else 'would enqueue'}")
    for day in missing:
        print(f"      {day.isoformat()}")
    if not missing:
        return 0
    if not args.enqueue:
        print("\nnothing queued (add --enqueue)")
        return 0

    for day in missing:
        enqueue_action(action, target_date=day)
    print(f"\nqueued {len(missing)} workers on {ACTION_ENTRY_ID}, "
          "one per day, run one at a time by the wrangler")
    return 0


if __name__ == '__main__':
    sys.exit(main())
