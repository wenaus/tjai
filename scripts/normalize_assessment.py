#!/usr/bin/env python3
"""Normalize assessment entry data to canonical field names.

Runs as post-processor after llm-assessment agent, or standalone for any date.

Usage:
    python normalize_assessment.py                # normalize most recent
    python normalize_assessment.py 2026-03-23     # normalize specific date
    python normalize_assessment.py all            # normalize all assessments

Canonical schema:
    data.scores: [{dt, score, cumulative, precis}, ...]
    data.total_turns, data.scored_events, data.final_cumulative, data.integral
"""
import sys

import bootstrap  # noqa: F401 - Django setup

from tjai_app.models import Entry


# Field name mappings: canonical <- variants
DT_KEYS = ('dt', 'datetime', 'time', 'timestamp')
SCORE_KEYS = ('score', 'delta', 'change', 'value')
CUM_KEYS = ('cumulative', 'cumul', 'cum', 'running_total')
PRECIS_KEYS = ('precis', 'description', 'summary', 'event', 'reason', 'detail')


def _pick(d, keys, default=None):
    """Return first matching key's value from dict."""
    for k in keys:
        if k in d:
            return d[k]
    return default


def normalize_entry(entry):
    """Normalize an assessment entry's data to canonical field names.

    Returns True if changes were made, False if already canonical.
    """
    data = entry.data if isinstance(entry.data, dict) else {}
    scores = data.get('scores', [])
    if not scores:
        return False

    changed = False

    # Normalize each score object
    canonical_scores = []
    running_cum = 0
    for s in scores:
        score_val = _pick(s, SCORE_KEYS, 0)
        cum_val = _pick(s, CUM_KEYS)
        dt_val = _pick(s, DT_KEYS, '')
        precis_val = _pick(s, PRECIS_KEYS, '')

        # Recompute cumulative if missing
        if cum_val is None:
            running_cum += score_val
            cum_val = running_cum
        else:
            running_cum = cum_val

        canonical = {
            'dt': dt_val,
            'score': score_val,
            'cumulative': cum_val,
            'precis': precis_val,
        }

        # Check if this score object differs from original
        if canonical != s:
            changed = True

        canonical_scores.append(canonical)

    # Normalize top-level fields
    total_turns = data.get('total_turns') or data.get('turn_count_estimated', 0)
    scored_events = data.get('scored_events') or len(canonical_scores)
    final_cum = canonical_scores[-1]['cumulative'] if canonical_scores else 0
    integral = sum(s['cumulative'] for s in canonical_scores)

    # Check if top-level fields need updating
    if (data.get('total_turns') != total_turns or
            data.get('scored_events') != scored_events or
            data.get('final_cumulative') != final_cum or
            data.get('integral') != integral or
            data.get('scores') != canonical_scores):
        changed = True

    if not changed:
        return False

    # Write back
    data['scores'] = canonical_scores
    data['total_turns'] = total_turns
    data['scored_events'] = scored_events
    data['final_cumulative'] = final_cum
    data['integral'] = integral

    # Clean up variant keys
    for old_key in ('final_score', 'turn_count_estimated', 'sessions_primary'):
        data.pop(old_key, None)

    entry.data = data
    entry.save(update_fields=['data'])
    return True


def normalize_date(date_str):
    """Normalize assessment for a specific date."""
    entry_id = f'assessment-{date_str}'
    entry = Entry.objects.filter(
        data__entry_id=entry_id, deleted_at__isnull=True,
    ).first()
    if not entry:
        print(f'{entry_id}: not found')
        return False
    if normalize_entry(entry):
        d = entry.data
        print(f'{entry_id}: normalized — {d["scored_events"]} events, '
              f'endpoint={d["final_cumulative"]}, integral={d["integral"]}')
        return True
    else:
        print(f'{entry_id}: already canonical')
        return False


def normalize_all():
    """Normalize all assessment entries."""
    entries = Entry.objects.filter(
        data__entry_id__startswith='assessment-',
        kind='memory',
        deleted_at__isnull=True,
    ).exclude(data__entry_id__contains='-prompt')
    count = 0
    for entry in entries:
        eid = (entry.data or {}).get('entry_id', '?')
        if normalize_entry(entry):
            d = entry.data
            print(f'{eid}: normalized — {d["scored_events"]} events, '
                  f'endpoint={d["final_cumulative"]}, integral={d["integral"]}')
            count += 1
        else:
            print(f'{eid}: already canonical')
    print(f'\n{count} entries normalized')


def main():
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == 'all':
            normalize_all()
        else:
            normalize_date(arg)
    else:
        # Most recent assessment
        entry = Entry.objects.filter(
            data__entry_id__startswith='assessment-',
            kind='memory',
            deleted_at__isnull=True,
        ).exclude(
            data__entry_id__contains='-prompt'
        ).order_by('-timestamp_modified').first()
        if entry:
            eid = (entry.data or {}).get('entry_id', '?')
            if normalize_entry(entry):
                d = entry.data
                print(f'{eid}: normalized — {d["scored_events"]} events, '
                      f'endpoint={d["final_cumulative"]}, integral={d["integral"]}')
            else:
                print(f'{eid}: already canonical')
        else:
            print('No assessment entries found')


if __name__ == '__main__':
    main()
