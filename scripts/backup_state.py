#!/usr/bin/env python3
"""Server-backup health state for Capcom (docs/capcom.md).

Produces the complete `server-backup` tile payload and emits a feed notice on
state transitions. The backup system owns the judgment — the thresholds live
here, next to the artifacts they judge — and the Capcom dispatcher stores the
returned payload verbatim.

The tile reads as size-and-age of the last backup: when healthy the value
carries the total dump size and the signed day-over-day growth vs the previous
backup at 0.1% resolution (`OK 156M +0.4%`), and the payload's `updated` field
is the backup's own completion time, so the tile's age display is time since
the backup itself. Problem states replace the value:

  FAILED           — the latest server-backup worker run failed
  STALE <n>h       — newest backup older than the 26-hour window
  INCOMPLETE <n>/<m> — fewer dumps than the trailing week's count, or a
                     zero-length dump
  +NN% / -NN%      — total size, or any major per-database dump, more than
                     20% from its trailing-week mean

Standalone: `python backup_state.py` prints the payload without touching
Capcom state.
"""
import json
import time
from pathlib import Path

BACKUP_ROOT = Path.home() / 'tjai-backups' / 'server'
CAPCOM_SOURCE = 'server-backup'
CAPCOM_URL = '/tjai/system/'
STALE_AFTER_HOURS = 26
SIZE_JUMP_FRACTION = 0.20
REFERENCE_DAYS = 7
PER_DUMP_FLOOR_BYTES = 5 * 1024 * 1024  # judge only dumps whose mean is >= 5MB


def _mb(n):
    return f"{n / (1024 * 1024):.0f}M"


def _dump_sizes(backup_dir):
    return {f.name: f.stat().st_size for f in backup_dir.glob('*-db.sql.gz')}


def build_payload(now=None):
    """Compute the tile payload plus transition metadata. Pure filesystem +
    worker-row read; no Capcom writes."""
    now = now or time.time()
    dirs = sorted((d for d in BACKUP_ROOT.iterdir() if d.is_dir()),
                  key=lambda d: d.name) if BACKUP_ROOT.is_dir() else []
    if not dirs:
        return {'source': CAPCOM_SOURCE, 'value': 'NO BACKUPS', 'color': 'red',
                'url': CAPCOM_URL, 'updated': now}, 'alarm', 'no backup directories exist'

    latest = dirs[-1]
    dumps = _dump_sizes(latest)
    total = sum(dumps.values())
    backup_time = max((f.stat().st_mtime for f in latest.glob('*-db.sql.gz')),
                      default=latest.stat().st_mtime)
    age_h = (now - backup_time) / 3600.0

    reference = dirs[-(REFERENCE_DAYS + 1):-1]
    ref_sizes = [_dump_sizes(d) for d in reference]
    ref_counts = [len(s) for s in ref_sizes if s]
    ref_totals = [sum(s.values()) for s in ref_sizes if s]

    def payload(value, color):
        return {'source': CAPCOM_SOURCE, 'value': value, 'color': color,
                'url': CAPCOM_URL, 'updated': backup_time}

    # The worker row is the authoritative record of the last run's outcome.
    last_error = None
    try:
        from tjai_app.models import WrangleWorker
        last_run = (WrangleWorker.objects
                    .filter(payload__action_entry_id=CAPCOM_SOURCE)
                    .order_by('-created_at').first())
        if last_run and last_run.status == 'failed':
            last_error = last_run.error or 'worker failed'
    except Exception:
        pass  # no Django context (standalone probe) — filesystem checks stand alone

    if last_error:
        return payload('FAILED', 'red'), 'alarm', last_error
    if age_h > STALE_AFTER_HOURS:
        return (payload(f'STALE {age_h:.0f}h', 'red'), 'warning',
                f'newest backup {latest.name} is {age_h:.0f}h old')
    expected = max(ref_counts, default=0)
    zero = sorted(name for name, size in dumps.items() if size == 0)
    if (expected and len(dumps) < expected) or zero:
        detail = (f'zero-length dumps: {", ".join(zero)}' if zero
                  else f'{len(dumps)} dumps, trailing week has {expected}')
        return (payload(f'INCOMPLETE {len(dumps)}/{max(expected, len(dumps))}',
                        'red'), 'warning', detail)

    worst_dev, worst_label = 0.0, None
    if ref_totals:
        mean = sum(ref_totals) / len(ref_totals)
        if mean > 0:
            dev = (total - mean) / mean
            if abs(dev) > abs(worst_dev):
                worst_dev, worst_label = dev, 'total'
    for name in dumps:
        history = [s[name] for s in ref_sizes if name in s]
        if not history:
            continue
        mean = sum(history) / len(history)
        if mean < PER_DUMP_FLOOR_BYTES:
            continue
        dev = (dumps[name] - mean) / mean
        if abs(dev) > abs(worst_dev):
            worst_dev, worst_label = dev, name
    if worst_label and abs(worst_dev) > SIZE_JUMP_FRACTION:
        return (payload(f'{worst_dev:+.0%} {_mb(total)}', 'amber'), 'warning',
                f'size deviation {worst_dev:+.0%} vs trailing-week mean ({worst_label})')

    # Healthy: total size plus day-over-day growth vs the previous backup.
    value = f'OK {_mb(total)}'
    if ref_totals and ref_totals[-1] > 0:
        value += f' {(total - ref_totals[-1]) / ref_totals[-1]:+.1%}'
    return payload(value, 'green'), 'info', ''


def collect_backup_state():
    """Dispatcher entry: store the tile and emit a notice on state transition."""
    from tjai_app import capcom

    new, severity, detail = build_payload()
    previous = (capcom.get_state().get(CAPCOM_SOURCE) or {}).get('value', '')
    prev_class, new_class = previous.split(' ')[0], new['value'].split(' ')[0]
    capcom.set_state(**new)

    if previous and new_class != prev_class:
        day = time.strftime('%Y-%m-%d')
        if new_class == 'OK':
            capcom.emit_notice(
                source=CAPCOM_SOURCE, severity='info',
                title=f"server backup recovered: {new['value']}",
                url=CAPCOM_URL, dedup_key=f'server-backup-recovered-{day}')
        else:
            capcom.emit_notice(
                source=CAPCOM_SOURCE, severity=severity,
                title=f"server backup: {new['value']}",
                url=CAPCOM_URL, dedup_key=f'server-backup-{new_class}-{day}',
                data={'detail': detail} if detail else None)


if __name__ == '__main__':
    import bootstrap  # noqa: F401 - Django setup for the worker-row check
    result, severity, detail = build_payload()
    print(json.dumps({'payload': result, 'severity': severity, 'detail': detail},
                     indent=2))
