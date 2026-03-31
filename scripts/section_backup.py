#!/usr/bin/env python3
"""Appends ## Backups to the daily synopsis entry."""
import logging
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup
from synopsis_utils import main_section

BACKUP_ROOT = Path.home() / 'tjai-backups' / 'server'
RECENT_DAYS = 7

logger = logging.getLogger('section_backups')


def build(since_ts, target_date):
    """Return markdown body or None."""
    if not BACKUP_ROOT.exists():
        return 'Backup directory not found.'

    day_dirs = sorted(
        [d for d in BACKUP_ROOT.iterdir() if d.is_dir() and len(d.name) == 10],
        reverse=True,
    )
    if not day_dirs:
        return 'No backups found.'

    total_days = len(day_dirs)
    lines = [f'{total_days} days archived']
    lines.append('')
    lines.append('| Date | Dump | Compressed | Data | Config |')
    lines.append('|------|-----:|-----------:|-----:|-------:|')

    for day_dir in day_dirs[:RECENT_DAYS]:
        date = day_dir.name
        gz_path = day_dir / 'tjai-db.sql.gz'
        if gz_path.exists():
            gz_size = gz_path.stat().st_size
            gz_mb = round(gz_size / (1024 * 1024), 1)
            try:
                with open(gz_path, 'rb') as f:
                    f.seek(-4, 2)
                    raw_size = int.from_bytes(f.read(4), 'little')
                raw_mb = round(raw_size / (1024 * 1024), 1)
            except Exception:
                raw_mb = '?'
            expected = ['env-www.env', 'env-home.env', 'etaverse.conf']
            config_ok = all((day_dir / f).exists() for f in expected)
            data_dir = day_dir / 'data'
            data_files = (
                sum(1 for _ in data_dir.rglob('*') if _.is_file())
                if data_dir.is_dir() else 0
            )
            lines.append(
                f'| {date} | {raw_mb} MB | {gz_mb} MB | '
                f'{data_files} | {"OK" if config_ok else "MISSING"} |'
            )
        else:
            lines.append(f'| {date} | MISSING | — | 0 | — |')

    return '\n'.join(lines)


if __name__ == '__main__':
    main_section('Backups', build)
