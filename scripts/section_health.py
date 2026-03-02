#!/usr/bin/env python3
"""Appends ## System Health to the daily synopsis entry.

Reads the health digest JSON written by health_digest.py (run as a
prerequisite in the daily-synopsis mechanical_script list).
"""
import json
import logging
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup
from synopsis_utils import main_section

DATA_DIR = Path(__file__).resolve().parent.parent / 'data' / 'health-digest'

logger = logging.getLogger('section_health')


def build(since_ts, target_date):
    """Return markdown body or None."""
    json_path = DATA_DIR / f'{target_date.isoformat()}.json'
    if not json_path.exists():
        logger.error("Health digest JSON not found: %s", json_path)
        return None

    try:
        data = json.loads(json_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read health JSON: %s", e)
        return None

    metrics = data.get('metrics', {})
    notes = data.get('notes', {})
    return _format_health_snapshot(metrics, notes)


def _format_health_snapshot(metrics, notes):
    """Format the mechanical health snapshot. Pure facts, no AI."""
    lines = []

    # --- Entries (sorted alphabetically) ---
    total = metrics.get('entries_total', '?')
    lines.append(f'**Entries:** {total} active')
    kind_counts = []
    for key, val in metrics.items():
        if key.startswith('entries_') and key != 'entries_total' \
                and not key.endswith('_24h') and isinstance(val, int):
            kind = key[len('entries_'):]
            kind_counts.append((kind, val))
    kind_counts.sort(key=lambda x: x[0])
    for kind, count in kind_counts:
        lines.append(f'- {kind}: {count:,}')
    lines.append('')

    # --- Storage ---
    db = metrics.get('db_size_mb', '?')
    backup_dump = metrics.get('backup_dump_mb', '?')
    backup_gz = metrics.get('backup_gz_mb', '?')
    backup_ok = metrics.get('backup_config_ok', 0)
    backup_files = metrics.get('backup_data_files', 0)
    lines.append(f'**Storage:** DB {db} MB | '
                 f'Backup {backup_dump} MB raw, '
                 f'{backup_gz} MB compressed | '
                 f'{backup_files} data files | '
                 f'config {"OK" if backup_ok else "MISSING"}')
    lines.append('')

    # --- System ---
    mem = metrics.get('mem_used_pct', '?')
    disk_gb = metrics.get('disk_used_gb', '?')
    disk_pct = metrics.get('disk_used_pct', '?')
    swap = metrics.get('swap_used_pct', '?')
    uptime = metrics.get('uptime_days', '?')
    load1 = metrics.get('cpu_load_1m', '?')
    load5 = metrics.get('cpu_load_5m', '?')
    lines.append(f'**System:** CPU load {load1}/{load5} (1m/5m) | '
                 f'Memory {mem}% | Swap {swap}% | '
                 f'Disk {disk_gb} GB ({disk_pct}%) | '
                 f'Uptime {uptime} days')
    lines.append('')

    # --- Agents ---
    action_stats = notes.get('action_stats', {})
    total_runs = metrics.get('agent_runs_total', 0)
    total_errors = metrics.get('agent_errors_total', 0)
    lines.append(f'**Agents:** {total_runs} runs, {total_errors} errors')
    if action_stats:
        for aid, stats in sorted(action_stats.items()):
            dur = stats['total_duration']
            dur_str = f'{dur / 60:.0f}m' if dur >= 60 else f'{dur:.0f}s'
            err_str = f' [{stats["errors"]} errors]' if stats['errors'] else ''
            lines.append(f'- {aid}: {stats["runs"]}x, {dur_str}{err_str}')
    lines.append('')

    # --- Activity ---
    created = metrics.get('entries_created_24h', 0)
    modified = metrics.get('entries_modified_24h', 0)
    research_done = metrics.get('research_completed_24h', 0)
    research_q = metrics.get('research_queue_depth', 0)
    cc = metrics.get('dialog_cc_turns_24h', 0)
    tg = metrics.get('dialog_tg_turns_24h', 0)
    lines.append(f'**Activity (24h):** {created} entries created, '
                 f'{modified} modified | '
                 f'{cc} CC dialog, {tg} TG dialog | '
                 f'{research_done} research completed '
                 f'({research_q} queued)')
    lines.append('')

    # --- Logs ---
    log_total = metrics.get('applog_entries_24h', 0)
    log_info = metrics.get('applog_info_24h', 0)
    log_warn = metrics.get('applog_warning_24h', 0)
    log_err = metrics.get('applog_error_24h', 0)
    lines.append(f'**Logs (24h):** {log_total} total — '
                 f'{log_info} info, {log_warn} warning, {log_err} error')

    recent_errors = notes.get('recent_errors', [])
    if recent_errors:
        lines.append('')
        lines.append(f'**Errors ({len(recent_errors)}):**')
        for err in recent_errors[:10]:
            lines.append(
                f'- [{err["time"]}] {err["source"]}: '
                f'{err["message"][:120]}')
    lines.append('')

    # --- Processes ---
    proc_names = {
        'proc_apache': 'Apache', 'proc_supervisord': 'Supervisord',
        'proc_action_agent': 'Action Agent',
        'proc_cloudwatch': 'CloudWatch',
    }
    procs_down = [name for key, name in proc_names.items()
                  if not metrics.get(key, 0)]
    if procs_down:
        lines.append(f'**PROCESSES DOWN:** {", ".join(procs_down)}')
        lines.append('')

    lines.append('[Full system status](/tjai/system/)')
    return '\n'.join(lines)


if __name__ == '__main__':
    main_section('System Health', build)
