#!/usr/bin/env python3
"""Devcloud stage-out bucket state for Capcom (docs/capcom.md).

The gateway's watch over the ePIC job-reporting bucket. The judgment
lives in the indexer that owns the data — swf-remote
`scripts/stageout_index.py`, which learns the bucket from S3 event
notifications rather than by listing it (swf-epicprod
docs/JOB_REPORTING.md) — and this reads its summary, stores the tile,
and emits a notice when the class changes.

The tile reads as the day's object count with the current hourly rate
(`OK 27 objs 19/h`). Problem states replace the value:

  RATE <n>/h      — objects per hour above the runaway ceiling
  LOOP <subject>  — one job writing more objects than any job should
  SWEEP STALLED   — the production sweeper has stopped reporting passes
  UNHEARD <n>     — objects in the bucket that no event announced
  STALE <n>m      — the event drain here has not run recently
  NO INDEX        — the indexer could not be read at all

Standalone: `python s3_reports_state.py` prints the payload without
touching Capcom state.
"""
import json
import subprocess
import time

INDEXER = '/home/admin/github/swf-remote/scripts/stageout_index.py'
CAPCOM_SOURCE = 's3-reports'
# The drain runs every five minutes; three misses is a stopped drain.
DRAIN_STALE_SECONDS = 20 * 60


def read_summary():
    """The indexer's own summary. Raises with its stderr on failure —
    a watch that fails quietly is worse than no watch."""
    result = subprocess.run(['/usr/bin/python3', INDEXER, 'summary'],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(
            f'stageout_index summary exited {result.returncode}: '
            f'{(result.stderr or result.stdout).strip()[:300]}')
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f'stageout_index returned invalid JSON: {result.stdout[:200]}'
        ) from exc


def build_payload(now=None):
    """Compute the tile payload plus transition metadata."""
    now = now or time.time()

    def payload(value, color):
        return {'source': CAPCOM_SOURCE, 'value': value, 'color': color,
                'updated': now}

    try:
        summary = read_summary()
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return payload('NO INDEX', 'red'), 'alarm', str(exc)

    last_drain = (summary.get('last_drain') or {}).get('at') or 0
    age = now - last_drain if last_drain else None
    hour = summary.get('last_hour') or 0
    day = summary.get('last_day') or 0

    if summary.get('verdict') == 'alarm':
        return (payload(f"RATE {hour}/h", 'red'), 'alarm',
                summary.get('detail', ''))
    if age is not None and age > DRAIN_STALE_SECONDS:
        return (payload(f'STALE {int(age // 60)}m', 'amber'), 'warning',
                'the event drain has not run recently')
    if summary.get('verdict') == 'warning':
        detail = summary.get('detail', '')
        if 'sweeper has reported no pass' in detail:
            # The production sweep is the bucket's normal drain. When it
            # stops, objects accumulate for a reason the write credential
            # would not fix, so this reads as a stalled drain rather than
            # growth and never touches the plug.
            return payload('SWEEP STALLED', 'amber'), 'warning', detail
        if 'per-job ceiling' in detail:
            return (payload(f"LOOP {summary.get('busiest_subject')}", 'amber'),
                    'warning', detail)
        unheard = (summary.get('last_reconcile') or {}).get('unheard') or 0
        return payload(f'UNHEARD {unheard}', 'amber'), 'warning', detail

    return payload(f'OK {day} objs {hour}/h', 'green'), 'info', ''


def collect_s3_reports():
    """Dispatcher entry: store the tile and emit a notice on transition."""
    from tjai_app import capcom

    new, severity, detail = build_payload()
    previous = (capcom.get_state().get(CAPCOM_SOURCE) or {}).get('value', '')
    prev_class = previous.split(' ')[0]
    new_class = new['value'].split(' ')[0]
    capcom.set_state(**new)

    if previous and new_class != prev_class:
        day = time.strftime('%Y-%m-%d')
        if new_class == 'OK':
            capcom.emit_notice(
                source=CAPCOM_SOURCE, severity='info',
                title=f"stage-out bucket recovered: {new['value']}",
                dedup_key=f's3-reports-recovered-{day}')
        else:
            capcom.emit_notice(
                source=CAPCOM_SOURCE, severity=severity,
                title=f"stage-out bucket: {new['value']}",
                dedup_key=f's3-reports-{new_class}-{day}',
                data={'detail': detail} if detail else None)


if __name__ == '__main__':
    result, severity, detail = build_payload()
    print(json.dumps({'payload': result, 'severity': severity,
                      'detail': detail}, indent=2))
