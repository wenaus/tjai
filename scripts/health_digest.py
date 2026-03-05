#!/usr/bin/env python3
"""Generate daily system health digest with 7-day averages.

Collects metrics from system, database, agents, logs, entries, and backups.
Writes:
  data/health-digest/YYYY-MM-DD.json  — raw metrics (for 7-day avg computation)
  data/health-digest/YYYY-MM-DD.md    — markdown report (for AI assessment)

Adding a new metric: add one line to a collector function, or add a new
collector to COLLECTORS. The framework handles averaging and formatting.

Usage:
    python health_digest.py              # today
    python health_digest.py 2026-03-01   # specific date
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup

from django.db import connection
from django.db.models import Count, Q

from tjai_app.db_log_handler import DbLogHandler
from tjai_app.models import Entry, AppLog, Tag

from datetime import timezone
UTC = timezone.utc

logger = logging.getLogger('health_digest')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='health_digest')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)

DATA_DIR = Path(__file__).resolve().parent.parent / 'data' / 'health-digest'


# ---------------------------------------------------------------------------
# Collectors
#
# Each collector returns either:
#   dict                — flat {metric_name: numeric_value}
#   (dict, dict)        — (metrics, notes) where notes hold non-numeric data
#
# Collectors that need a time window receive `since_ts` (epoch float).
# Point-in-time collectors accept it but may ignore it.
# ---------------------------------------------------------------------------

def collect_system(since_ts):
    """System metrics from /proc."""
    metrics = {}

    with open('/proc/uptime') as f:
        metrics['uptime_days'] = round(float(f.read().split()[0]) / 86400, 1)

    with open('/proc/loadavg') as f:
        parts = f.read().split()
    metrics['cpu_load_1m'] = float(parts[0])
    metrics['cpu_load_5m'] = float(parts[1])

    meminfo = {}
    with open('/proc/meminfo') as f:
        for line in f:
            kv = line.split(':')
            if len(kv) == 2:
                meminfo[kv[0].strip()] = int(kv[1].strip().split()[0])
    total = meminfo.get('MemTotal', 1)
    avail = meminfo.get('MemAvailable', 0)
    swap_total = meminfo.get('SwapTotal', 0)
    swap_used = swap_total - meminfo.get('SwapFree', 0)

    metrics['mem_used_pct'] = round((1 - avail / total) * 100, 1) if total else 0
    metrics['swap_used_pct'] = round(swap_used / swap_total * 100, 1) if swap_total else 0

    st = os.statvfs('/')
    total_gb = (st.f_frsize * st.f_blocks) / (1024 ** 3)
    used_gb = (st.f_frsize * (st.f_blocks - st.f_bfree)) / (1024 ** 3)
    metrics['disk_used_pct'] = round(used_gb / total_gb * 100, 1) if total_gb else 0
    metrics['disk_used_gb'] = round(used_gb, 1)

    return metrics


def collect_database(since_ts):
    """PostgreSQL metrics."""
    metrics = {}
    with connection.cursor() as cur:
        cur.execute("SELECT pg_database_size('tjai')")
        metrics['db_size_mb'] = round(cur.fetchone()[0] / (1024 * 1024), 1)

        cur.execute("""
            SELECT numbackends, blks_read, blks_hit
            FROM pg_stat_database WHERE datname = 'tjai'
        """)
        row = cur.fetchone()
        if row:
            metrics['db_connections'] = row[0]
            blks_read, blks_hit = row[1], row[2]
            total = blks_read + blks_hit
            metrics['db_cache_hit_pct'] = round(
                blks_hit / total * 100, 2) if total else 100.0
    return metrics


def collect_backups(since_ts):
    """Backup sizes and status from Dropbox."""
    metrics = {}
    backup_root = Path.home() / 'Dropbox' / 'tjai-backups' / 'server'

    if not backup_root.exists():
        return {'backup_dump_mb': 0, 'backup_gz_mb': 0,
                'backup_data_files': 0, 'backup_config_ok': 0}

    day_dirs = sorted(
        [d for d in backup_root.iterdir() if d.is_dir() and len(d.name) == 10],
        reverse=True,
    )
    if not day_dirs:
        return {'backup_dump_mb': 0, 'backup_gz_mb': 0,
                'backup_data_files': 0, 'backup_config_ok': 0}

    latest = day_dirs[0]
    gz_path = latest / 'tjai-db.sql.gz'
    if gz_path.exists():
        gz_size = gz_path.stat().st_size
        metrics['backup_gz_mb'] = round(gz_size / (1024 * 1024), 1)
        try:
            with open(gz_path, 'rb') as f:
                f.seek(-4, 2)
                raw_size = int.from_bytes(f.read(4), 'little')
            metrics['backup_dump_mb'] = round(raw_size / (1024 * 1024), 1)
        except Exception:
            metrics['backup_dump_mb'] = 0
    else:
        metrics['backup_dump_mb'] = 0
        metrics['backup_gz_mb'] = 0

    expected = ['env-www.env', 'env-home.env', 'etaverse.conf']
    metrics['backup_config_ok'] = 1 if all(
        (latest / f).exists() for f in expected) else 0

    data_dir = latest / 'data'
    metrics['backup_data_files'] = (
        sum(1 for _ in data_dir.rglob('*') if _.is_file())
        if data_dir.is_dir() else 0
    )

    return metrics


def collect_agents(since_ts):
    """Agent run stats from AppLog (past 24h).

    Only counts actual completion summary lines (containing 'exit_code='),
    not individual stderr output lines which also log under agent_complete.
    """
    metrics = {}
    notes = {}
    since_dt = datetime.fromtimestamp(since_ts, tz=UTC)

    # Only completion summary lines have exit_code in the message
    completions = AppLog.objects.filter(
        source='agent_complete',
        timestamp__gte=since_dt,
        message__contains='exit_code=',
    )

    action_stats = {}
    for log in completions:
        extra = log.extra_data or {}
        action_id = extra.get('action_id')
        if not action_id:
            logger.warning("agent_complete log missing action_id: %s",
                           log.message[:100])
            continue
        if action_id not in action_stats:
            action_stats[action_id] = {
                'runs': 0, 'total_duration': 0, 'errors': 0}
        action_stats[action_id]['runs'] += 1
        dur = extra.get('duration_sec')
        if dur:
            action_stats[action_id]['total_duration'] += float(dur)
        status = extra.get('run_status', '')
        if status in ('failed', 'timeout', 'error'):
            action_stats[action_id]['errors'] += 1

    total_runs = 0
    total_errors = 0
    for aid, stats in action_stats.items():
        safe_aid = aid.replace('-', '_')
        metrics[f'agent_{safe_aid}_runs'] = stats['runs']
        metrics[f'agent_{safe_aid}_duration_sec'] = round(
            stats['total_duration'], 1)
        metrics[f'agent_{safe_aid}_errors'] = stats['errors']
        total_runs += stats['runs']
        total_errors += stats['errors']

    metrics['agent_runs_total'] = total_runs
    metrics['agent_errors_total'] = total_errors

    if action_stats:
        notes['action_stats'] = action_stats

    return metrics, notes


def collect_logs(since_ts):
    """AppLog stats for the past 24h."""
    metrics = {}
    notes = {}
    since_dt = datetime.fromtimestamp(since_ts, tz=UTC)

    metrics['applog_entries_24h'] = AppLog.objects.filter(
        timestamp__gte=since_dt).count()

    # Counts by level
    level_counts = dict(
        AppLog.objects.filter(timestamp__gte=since_dt)
        .values_list('levelname').annotate(c=Count('id'))
    )
    metrics['applog_info_24h'] = level_counts.get('INFO', 0)
    metrics['applog_warning_24h'] = level_counts.get('WARNING', 0)
    metrics['applog_error_24h'] = level_counts.get('ERROR', 0)
    metrics['applog_errors_24h'] = (level_counts.get('ERROR', 0)
                                    + level_counts.get('CRITICAL', 0))

    errors = AppLog.objects.filter(
        timestamp__gte=since_dt,
        level__gte=logging.ERROR,
    )

    error_msgs = list(errors.order_by('-timestamp').values_list(
        'timestamp', 'source', 'message'
    )[:20])
    if error_msgs:
        notes['recent_errors'] = [
            {'time': ts.strftime('%H:%M'), 'source': src,
             'message': msg[:200]}
            for ts, src, msg in error_msgs
        ]

    return metrics, notes


def collect_entries(since_ts):
    """tjai entry stats."""
    metrics = {}

    metrics['entries_created_24h'] = Entry.objects.filter(
        timestamp_created__gte=since_ts, deleted_at__isnull=True
    ).count()
    metrics['entries_modified_24h'] = Entry.objects.filter(
        timestamp_modified__gte=since_ts, deleted_at__isnull=True
    ).count()
    metrics['entries_total'] = Entry.objects.filter(
        deleted_at__isnull=True).count()

    kind_counts = dict(
        Entry.objects.filter(deleted_at__isnull=True)
        .values_list('kind').annotate(c=Count('id'))
    )
    for kind, count in kind_counts.items():
        metrics[f'entries_{kind}'] = count

    return metrics


def collect_dialog(since_ts):
    """Dialog turn counts by source."""
    metrics = {}

    # Claude Code turns: entries created via MCP
    metrics['dialog_cc_turns_24h'] = Entry.objects.filter(
        timestamp_created__gte=since_ts,
        deleted_at__isnull=True,
    ).filter(
        Q(data__source='mcp') | Q(data__source='claude_code')
    ).count()

    # Telegram turns
    metrics['dialog_tg_turns_24h'] = Entry.objects.filter(
        timestamp_created__gte=since_ts,
        deleted_at__isnull=True,
        data__source='telegram',
    ).count()

    return metrics



def collect_research(since_ts):
    """Research pipeline stats."""
    metrics = {}

    metrics['research_completed_24h'] = Entry.objects.filter(
        timestamp_modified__gte=since_ts,
        deleted_at__isnull=True,
        tags__tag_name='research_topic',
        status='done',
    ).distinct().count()

    metrics['research_queue_depth'] = Entry.objects.filter(
        deleted_at__isnull=True,
        tags__tag_name='research_topic',
    ).exclude(
        status='done'
    ).exclude(
        status='archive'
    ).distinct().count()

    return metrics



def collect_processes(since_ts):
    """Key process status (1=running, 0=down)."""
    targets = {
        'proc_apache': 'apache2',
        'proc_supervisord': 'supervisord',
        'proc_action_agent': 'action_agent',
        'proc_cloudwatch': 'amazon-cloudwatch-agent',
    }
    found = {k: 0 for k in targets}
    for pid_dir in os.listdir('/proc'):
        if not pid_dir.isdigit():
            continue
        try:
            with open(f'/proc/{pid_dir}/cmdline', 'rb') as f:
                cmdline = f.read().decode('utf-8', errors='replace')
            for metric_key, search_term in targets.items():
                if search_term in cmdline:
                    found[metric_key] = 1
        except (PermissionError, FileNotFoundError):
            continue
    return found


# ---------------------------------------------------------------------------
# Collector registry
#
# (name, function, returns_notes)
# returns_notes=True means function returns (metrics, notes) tuple
# returns_notes=False means function returns just metrics dict
#
# To add a new collector: append one tuple here.
# ---------------------------------------------------------------------------

COLLECTORS = [
    ('system',    collect_system,    False),
    ('database',  collect_database,  False),
    ('backups',   collect_backups,   False),
    ('processes', collect_processes, False),
    ('entries',   collect_entries,   False),
    ('dialog',    collect_dialog,    False),
    ('research',  collect_research,  False),
    ('agents',    collect_agents,    True),
    ('logs',      collect_logs,      True),
]


# ---------------------------------------------------------------------------
# 7-day average computation
# ---------------------------------------------------------------------------

def load_prior_days(target_date, days=7):
    """Load metric JSONs for the N days before target_date."""
    prior = []
    for i in range(1, days + 1):
        d = target_date - timedelta(days=i)
        path = DATA_DIR / f'{d.isoformat()}.json'
        if path.exists():
            try:
                data = json.loads(path.read_text())
                prior.append(data.get('metrics', {}))
            except (json.JSONDecodeError, KeyError):
                continue
    return prior


def compute_averages(prior_days):
    """Compute 7-day averages for all numeric metrics found in prior days."""
    if not prior_days:
        return {}

    averages = {}
    all_keys = set()
    for day in prior_days:
        all_keys.update(day.keys())

    for key in all_keys:
        values = []
        for day in prior_days:
            val = day.get(key)
            if isinstance(val, (int, float)):
                values.append(val)
        if values:
            averages[key] = round(sum(values) / len(values), 2)

    return averages


# ---------------------------------------------------------------------------
# Markdown report generation
# ---------------------------------------------------------------------------

def _metric_row(label, key, metrics, averages, fmt='.1f', suffix=''):
    """Format a single metric table row with today + 7-day avg."""
    val = metrics.get(key)
    if val is None:
        return None
    val_fmt = round(val) if fmt == 'd' and isinstance(val, float) else val
    val_str = f'{val_fmt:{fmt}}{suffix}'
    avg = averages.get(key)
    if avg is not None:
        # Averages are always float; round for integer formats
        avg_fmt = round(avg) if fmt == 'd' else avg
        if avg != 0:
            pct_change = (val - avg) / avg * 100
            sign = '+' if pct_change > 0 else ''
            avg_str = f'{avg_fmt:{fmt}}{suffix} ({sign}{pct_change:.1f}%)'
        else:
            avg_str = f'{avg_fmt:{fmt}}{suffix}'
    else:
        avg_str = '\u2014'
    return f'| {label} | {val_str} | {avg_str} |'


def format_markdown(target_date, metrics, averages, notes):
    """Generate the full markdown health report."""
    date_str = target_date.strftime('%a %b %-d, %Y')
    lines = [f'# System Health Digest: {date_str}', '']

    # --- System ---
    lines.append('## System')
    lines.append('| Metric | Today | 7-day avg |')
    lines.append('|---|---|---|')
    for label, key, fmt, suffix in [
        ('CPU load (1m)', 'cpu_load_1m', '.2f', ''),
        ('CPU load (5m)', 'cpu_load_5m', '.2f', ''),
        ('Memory used', 'mem_used_pct', '.1f', '%'),
        ('Swap used', 'swap_used_pct', '.1f', '%'),
        ('Disk used', 'disk_used_pct', '.1f', '%'),
        ('Disk used', 'disk_used_gb', '.1f', ' GB'),
        ('Uptime', 'uptime_days', '.1f', ' days'),
    ]:
        row = _metric_row(label, key, metrics, averages, fmt, suffix)
        if row:
            lines.append(row)
    lines.append('')

    # --- Database ---
    lines.append('## Database')
    lines.append('| Metric | Today | 7-day avg |')
    lines.append('|---|---|---|')
    for label, key, fmt, suffix in [
        ('DB size', 'db_size_mb', '.1f', ' MB'),
        ('Cache hit ratio', 'db_cache_hit_pct', '.2f', '%'),
        ('Connections', 'db_connections', 'd', ''),
    ]:
        row = _metric_row(label, key, metrics, averages, fmt, suffix)
        if row:
            lines.append(row)
    lines.append('')

    # --- Backups ---
    lines.append('## Backups')
    lines.append('| Metric | Today | 7-day avg |')
    lines.append('|---|---|---|')
    for label, key, fmt, suffix in [
        ('Dump size (raw)', 'backup_dump_mb', '.1f', ' MB'),
        ('Compressed size', 'backup_gz_mb', '.1f', ' MB'),
        ('Data files', 'backup_data_files', 'd', ''),
        ('Config files OK', 'backup_config_ok', 'd', ''),
    ]:
        row = _metric_row(label, key, metrics, averages, fmt, suffix)
        if row:
            lines.append(row)
    lines.append('')

    # --- Agent Activity ---
    action_stats = notes.get('action_stats', {})
    lines.append('## Agent Activity (past 24h)')
    lines.append(
        f'Total runs: {metrics.get("agent_runs_total", 0)}, '
        f'Errors: {metrics.get("agent_errors_total", 0)}')
    lines.append('')
    if action_stats:
        lines.append('| Action | Runs | Duration | Errors |')
        lines.append('|---|---|---|---|')
        for aid, stats in sorted(action_stats.items()):
            dur = stats['total_duration']
            dur_str = f'{dur / 60:.1f}m' if dur >= 60 else f'{dur:.0f}s'
            err_str = str(stats['errors']) if stats['errors'] else '\u2014'
            lines.append(
                f'| {aid} | {stats["runs"]} | {dur_str} | {err_str} |')
        lines.append('')

    # --- Entries ---
    lines.append('## tjai Entries')
    lines.append('| Metric | Today | 7-day avg |')
    lines.append('|---|---|---|')
    for label, key, fmt, suffix in [
        ('Created (24h)', 'entries_created_24h', 'd', ''),
        ('Modified (24h)', 'entries_modified_24h', 'd', ''),
        ('Total active', 'entries_total', 'd', ''),
    ]:
        row = _metric_row(label, key, metrics, averages, fmt, suffix)
        if row:
            lines.append(row)
    lines.append('')

    # --- Dialog ---
    lines.append('## Dialog')
    lines.append('| Metric | Today | 7-day avg |')
    lines.append('|---|---|---|')
    for label, key, fmt, suffix in [
        ('Claude Code turns', 'dialog_cc_turns_24h', 'd', ''),
        ('Telegram turns', 'dialog_tg_turns_24h', 'd', ''),
    ]:
        row = _metric_row(label, key, metrics, averages, fmt, suffix)
        if row:
            lines.append(row)
    lines.append('')

    # --- Research ---
    lines.append('## Research')
    lines.append('| Metric | Today | 7-day avg |')
    lines.append('|---|---|---|')
    for label, key, fmt, suffix in [
        ('Research completed', 'research_completed_24h', 'd', ''),
        ('Research queue', 'research_queue_depth', 'd', ''),
    ]:
        row = _metric_row(label, key, metrics, averages, fmt, suffix)
        if row:
            lines.append(row)
    lines.append('')

    # --- Log Activity ---
    lines.append('## Log Activity')
    lines.append('| Metric | Today | 7-day avg |')
    lines.append('|---|---|---|')
    for label, key, fmt, suffix in [
        ('Log entries (24h)', 'applog_entries_24h', 'd', ''),
        ('Errors (24h)', 'applog_errors_24h', 'd', ''),
    ]:
        row = _metric_row(label, key, metrics, averages, fmt, suffix)
        if row:
            lines.append(row)
    lines.append('')

    # --- Processes ---
    lines.append('## Processes')
    proc_names = {
        'proc_apache': 'Apache',
        'proc_supervisord': 'Supervisord',
        'proc_action_agent': 'Action Agent',
        'proc_cloudwatch': 'CloudWatch Agent',
    }
    for key, name in proc_names.items():
        val = metrics.get(key, 0)
        status = 'running' if val else 'DOWN'
        lines.append(f'- {name}: **{status}**')
    lines.append('')

    # --- Errors ---
    recent_errors = notes.get('recent_errors', [])
    if recent_errors:
        lines.append('## Errors (past 24h)')
        for err in recent_errors:
            lines.append(
                f'- [{err["time"]}] {err["source"]}: {err["message"]}')
        lines.append('')

    return '\n'.join(lines)



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(date_str=None):
    """Collect health metrics and write JSON + MD files.

    Called by daily_sections.py or directly via CLI.
    """
    if date_str:
        target = datetime.strptime(date_str, '%Y-%m-%d').date()
    else:
        from tjai_app.services import get_timezone
        target = datetime.now(get_timezone()).date()

    logger.info("Generating health digest for %s", target.isoformat())

    since_ts = time.time() - 86400

    # Run all collectors
    all_metrics = {}
    all_notes = {}

    for name, collector_fn, returns_notes in COLLECTORS:
        try:
            logger.info("Collecting: %s", name)
            if returns_notes:
                m, n = collector_fn(since_ts)
                all_metrics.update(m)
                all_notes.update(n)
            else:
                all_metrics.update(collector_fn(since_ts))
        except Exception as e:
            logger.error("Collector '%s' failed: %s", name, e)

    # 7-day averages from prior days' JSON files
    prior = load_prior_days(target)
    averages = compute_averages(prior)

    # Write JSON (for future averaging and daily_sections consumption)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_path = DATA_DIR / f'{target.isoformat()}.json'
    json_data = {
        'date': target.isoformat(),
        'generated': datetime.now().isoformat(),
        'metrics': all_metrics,
        'averages': averages,
        'avg_days': len(prior),
        'notes': {k: v for k, v in all_notes.items()
                  if isinstance(v, (list, dict))},
    }
    json_path.write_text(json.dumps(json_data, indent=2, default=str))
    logger.info("Wrote %s", json_path)

    # Write Markdown (for AI consumption)
    md_path = DATA_DIR / f'{target.isoformat()}.md'
    md_content = format_markdown(target, all_metrics, averages, all_notes)
    md_path.write_text(md_content)
    logger.info("Wrote %s", md_path)

    logger.info("Digest complete: %d metrics, %d-day averages",
                len(all_metrics), len(prior))


def main():
    parser = argparse.ArgumentParser(
        description='Generate daily health digest')
    parser.add_argument(
        'date', nargs='?', default=None,
        help='Target date YYYY-MM-DD (default: today)')
    args = parser.parse_args()
    run(date_str=args.date)


if __name__ == '__main__':
    main()
