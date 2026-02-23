#!/usr/bin/env python3
"""Collect system health metrics and write to sysconfig.

Reads system, PostgreSQL, process, and CloudWatch metrics.
Writes two sysconfig keys:
  system_health_status — "green", "yellow", or "red"
  system_health_data   — JSON with full metrics + timestamp
"""
import json
import os
import time
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup

from django.db import connection
from django.db.models import Count
from tjai_app.models import Entry, Context, TagStats, Machine, SysConfig, AppLog

INSTANCE_ID = 'i-0a5a34f8ccd4429df'


def collect_system():
    """Collect system metrics from /proc and os."""
    metrics = {}

    # Uptime
    with open('/proc/uptime') as f:
        uptime_sec = float(f.read().split()[0])
    metrics['uptime_days'] = round(uptime_sec / 86400, 1)

    # Load average
    with open('/proc/loadavg') as f:
        parts = f.read().split()
    metrics['load_1m'] = float(parts[0])
    metrics['load_5m'] = float(parts[1])
    metrics['load_15m'] = float(parts[2])

    # CPU count
    metrics['cpu_count'] = os.cpu_count() or 1

    # Memory from /proc/meminfo
    meminfo = {}
    with open('/proc/meminfo') as f:
        for line in f:
            kv = line.split(':')
            if len(kv) == 2:
                meminfo[kv[0].strip()] = int(kv[1].strip().split()[0])
    total_mb = meminfo.get('MemTotal', 0) / 1024
    available_mb = meminfo.get('MemAvailable', 0) / 1024
    swap_total_mb = meminfo.get('SwapTotal', 0) / 1024
    swap_used_mb = (meminfo.get('SwapTotal', 0) - meminfo.get('SwapFree', 0)) / 1024

    metrics['mem_total_mb'] = round(total_mb)
    metrics['mem_available_mb'] = round(available_mb)
    metrics['mem_available_pct'] = round(available_mb / total_mb * 100, 1) if total_mb else 0
    metrics['swap_total_mb'] = round(swap_total_mb)
    metrics['swap_used_mb'] = round(swap_used_mb)
    metrics['swap_used_pct'] = round(swap_used_mb / swap_total_mb * 100, 1) if swap_total_mb else 0

    # Disk usage
    st = os.statvfs('/')
    disk_total_gb = (st.f_frsize * st.f_blocks) / (1024 ** 3)
    disk_used_gb = (st.f_frsize * (st.f_blocks - st.f_bfree)) / (1024 ** 3)
    disk_used_pct = disk_used_gb / disk_total_gb * 100 if disk_total_gb else 0

    metrics['disk_total_gb'] = round(disk_total_gb, 1)
    metrics['disk_used_gb'] = round(disk_used_gb, 1)
    metrics['disk_used_pct'] = round(disk_used_pct, 1)

    return metrics


def collect_postgres():
    """Collect PostgreSQL stats via Django's DB connection."""
    metrics = {}

    with connection.cursor() as cur:
        # Database stats
        cur.execute("""
            SELECT numbackends, xact_commit, xact_rollback,
                   blks_read, blks_hit, tup_returned, tup_fetched,
                   tup_inserted, tup_updated, tup_deleted, deadlocks
            FROM pg_stat_database WHERE datname = 'tjai'
        """)
        row = cur.fetchone()
        if row:
            metrics['connections'] = row[0]
            metrics['xact_commit'] = row[1]
            metrics['xact_rollback'] = row[2]
            blks_read, blks_hit = row[3], row[4]
            total_blks = blks_read + blks_hit
            metrics['cache_hit_ratio'] = round(blks_hit / total_blks * 100, 2) if total_blks else 100.0
            metrics['tup_returned'] = row[5]
            metrics['tup_fetched'] = row[6]
            metrics['tup_inserted'] = row[7]
            metrics['tup_updated'] = row[8]
            metrics['tup_deleted'] = row[9]
            metrics['deadlocks'] = row[10]

        # Database size
        cur.execute("SELECT pg_database_size('tjai')")
        metrics['db_size_mb'] = round(cur.fetchone()[0] / (1024 * 1024), 1)

        # Top 5 tables by activity
        cur.execute("""
            SELECT relname, seq_scan, idx_scan,
                   n_tup_ins, n_tup_upd, n_tup_del, n_live_tup
            FROM pg_stat_user_tables
            ORDER BY (n_tup_ins + n_tup_upd + n_tup_del) DESC
            LIMIT 5
        """)
        metrics['top_tables'] = [
            {
                'name': r[0], 'seq_scan': r[1], 'idx_scan': r[2],
                'inserts': r[3], 'updates': r[4], 'deletes': r[5],
                'live_rows': r[6],
            }
            for r in cur.fetchall()
        ]

    return metrics


def collect_processes():
    """Scan /proc for key processes."""
    counts = {
        'apache2': 0,
        'cloudwatch-agent': 0,
        'action_agent': 0,
        'supervisord': 0,
    }

    for pid_dir in os.listdir('/proc'):
        if not pid_dir.isdigit():
            continue
        try:
            with open(f'/proc/{pid_dir}/cmdline', 'rb') as f:
                cmdline = f.read().decode('utf-8', errors='replace').replace('\x00', ' ')
            if 'apache2' in cmdline:
                counts['apache2'] += 1
            if 'amazon-cloudwatch-agent' in cmdline:
                counts['cloudwatch-agent'] += 1
            if 'action_agent' in cmdline:
                counts['action_agent'] += 1
            if 'supervisord' in cmdline:
                counts['supervisord'] += 1
        except (PermissionError, FileNotFoundError):
            continue

    return counts


def collect_cloudwatch():
    """Fetch 24h CloudWatch metrics via boto3."""
    try:
        import boto3
        from datetime import datetime, timedelta

        cw = boto3.client('cloudwatch', region_name='us-east-1')
        end = datetime.utcnow()
        start = end - timedelta(hours=24)

        metrics = {}

        # CPU Utilization from AWS/EC2
        resp = cw.get_metric_statistics(
            Namespace='AWS/EC2',
            MetricName='CPUUtilization',
            Dimensions=[{'Name': 'InstanceId', 'Value': INSTANCE_ID}],
            StartTime=start,
            EndTime=end,
            Period=3600,
            Statistics=['Average'],
        )
        cpu_points = sorted(resp['Datapoints'], key=lambda d: d['Timestamp'])
        metrics['cpu_hourly'] = [
            {'hour': d['Timestamp'].strftime('%H:%M'), 'avg': round(d['Average'], 1)}
            for d in cpu_points
        ]
        if cpu_points:
            metrics['cpu_avg_24h'] = round(
                sum(d['Average'] for d in cpu_points) / len(cpu_points), 1
            )

        # Try CWAgent metrics (may not be publishing yet)
        for metric_name in ['mem_used_percent', 'swap_used_percent', 'disk_used_percent']:
            try:
                resp = cw.get_metric_statistics(
                    Namespace='CWAgent',
                    MetricName=metric_name,
                    Dimensions=[{'Name': 'InstanceId', 'Value': INSTANCE_ID}],
                    StartTime=start,
                    EndTime=end,
                    Period=3600,
                    Statistics=['Average'],
                )
                points = sorted(resp['Datapoints'], key=lambda d: d['Timestamp'])
                if points:
                    metrics[f'{metric_name}_hourly'] = [
                        {'hour': d['Timestamp'].strftime('%H:%M'), 'avg': round(d['Average'], 1)}
                        for d in points
                    ]
            except Exception:
                pass

        return metrics
    except Exception as e:
        return {'error': str(e)}


def _pid_alive(pid_str):
    """Check if a PID is alive by probing /proc."""
    try:
        return os.path.exists(f'/proc/{int(pid_str)}')
    except (ValueError, TypeError):
        return False


def _find_proc(search_term):
    """Find a process PID by scanning /proc cmdlines.

    Avoids reliance on /tmp PID files which are invisible under
    Apache's PrivateTmp=yes namespace.
    """
    for pid_dir in os.listdir('/proc'):
        if not pid_dir.isdigit():
            continue
        try:
            with open(f'/proc/{pid_dir}/cmdline', 'rb') as f:
                cmdline = f.read().decode('utf-8', errors='replace')
            if search_term in cmdline:
                return pid_dir
        except (PermissionError, FileNotFoundError):
            continue
    return None


def _collect_agents(now):
    """Check health of tjai agent processes."""
    agents = []

    # Action agent — PID + heartbeat from sysconfig
    pid = SysConfig.objects.filter(key='action_agent_pid').values_list('value', flat=True).first()
    hb = SysConfig.objects.filter(key='action_agent_heartbeat').values_list('value', flat=True).first()
    alive = _pid_alive(pid) if pid else False
    hb_min = round((now - float(hb)) / 60, 1) if hb else None
    agents.append({
        'name': 'Action Agent',
        'pid': pid,
        'alive': alive,
        'heartbeat_min': hb_min,
        'status': 'running' if alive and hb_min and hb_min < 10 else 'stale' if alive else 'down',
    })

    # AI agents — status, health, errors from sysconfig
    for agent_name, agent_id in [('Research Agent', 'research-agent'),
                                  ('Picks Agent', 'picks-agent')]:
        a_status = SysConfig.objects.filter(
            key=f'agent_{agent_id}_status'
        ).values_list('value', flat=True).first() or 'idle'
        a_launched = SysConfig.objects.filter(
            key=f'agent_{agent_id}_launched'
        ).values_list('value', flat=True).first()
        a_completed = SysConfig.objects.filter(
            key=f'agent_{agent_id}_completed'
        ).values_list('value', flat=True).first()
        a_health = SysConfig.objects.filter(
            key=f'agent_{agent_id}_health'
        ).values_list('value', flat=True).first()
        a_last_activity = SysConfig.objects.filter(
            key=f'agent_{agent_id}_last_activity'
        ).values_list('value', flat=True).first()
        a_error = SysConfig.objects.filter(
            key=f'agent_{agent_id}_last_error'
        ).values_list('value', flat=True).first()
        a_error_time = SysConfig.objects.filter(
            key=f'agent_{agent_id}_last_error_time'
        ).values_list('value', flat=True).first()

        detail = {}
        if a_status == 'running' and a_launched:
            detail['running_min'] = round((now - float(a_launched)) / 60, 1)
        if a_completed:
            detail['completed_min'] = round((now - float(a_completed)) / 60, 1)
        if a_health:
            detail['health'] = a_health
        if a_last_activity:
            detail['last_activity_min'] = round((now - float(a_last_activity)) / 60, 1)
        if a_error:
            detail['last_error'] = a_error
        if a_error_time:
            detail['last_error_min'] = round((now - float(a_error_time)) / 60, 1)

        agents.append({
            'name': agent_name,
            'status': a_status,
            **detail,
        })

    # Telegram bot and Supervisord — scan /proc instead of /tmp PID files
    # (Apache's PrivateTmp=yes makes /tmp PID files invisible to subprocesses)
    for name, search_term in [('Telegram Bot', 'tg_bot'), ('Supervisord', 'supervisord')]:
        found_pid = _find_proc(search_term)
        agents.append({
            'name': name,
            'pid': found_pid,
            'alive': found_pid is not None,
            'status': 'running' if found_pid else 'down',
        })

    return agents


def collect_backups():
    """Check tjai backup health in Dropbox."""
    backup_root = Path.home() / 'Dropbox' / 'tjai-backups' / 'server'
    result = {
        'path': str(backup_root),
        'exists': backup_root.exists(),
        'total_days': 0,
        'latest': None,
    }

    if not backup_root.exists():
        return result

    # Find all date directories (YYYY-MM-DD pattern)
    day_dirs = sorted(
        [d for d in backup_root.iterdir() if d.is_dir() and len(d.name) == 10],
        reverse=True,
    )
    result['total_days'] = len(day_dirs)

    if not day_dirs:
        return result

    latest = day_dirs[0]
    expected_files = ['tjai-db.sql.gz', 'env-www.env', 'env-home.env', 'etaverse.conf']
    found = {}
    for name in expected_files:
        p = latest / name
        if p.exists():
            found[name] = p.stat().st_size
        else:
            found[name] = None

    # Check data directory
    data_dir = latest / 'data'
    data_files = list(data_dir.rglob('*')) if data_dir.is_dir() else []
    data_file_count = sum(1 for f in data_files if f.is_file())

    db_size = found.get('tjai-db.sql.gz')
    result['latest'] = {
        'date': latest.name,
        'files': found,
        'data_files': data_file_count,
        'db_size_mb': round(db_size / (1024 * 1024), 1) if db_size else None,
        'all_present': all(v is not None for v in found.values()) and data_file_count > 0,
    }

    return result


def collect_dropbox():
    """Check Dropbox health via dropbox.py, auto-start if down."""
    import subprocess

    result = {
        'running': False,
        'status': 'unknown',
        'restarted': False,
    }

    try:
        proc = subprocess.run(
            [str(Path.home() / 'bin' / 'dropbox.py'), 'status'],
            capture_output=True, text=True, timeout=10,
        )
        status_text = proc.stdout.strip()
        result['status'] = status_text or 'unknown'
        result['running'] = "isn't running" not in status_text
    except Exception as e:
        result['status'] = f'status check failed: {e}'
        return result

    # Auto-start if not running
    if not result['running']:
        try:
            subprocess.run(
                [str(Path.home() / 'bin' / 'dropbox.py'), 'start'],
                capture_output=True, text=True, timeout=15,
            )
            result['restarted'] = True
        except Exception:
            pass
        return result

    # Backup directory freshness
    backup_dir = str(Path.home() / 'Dropbox' / 'Current' / 'tjai_backups')
    try:
        files = os.listdir(backup_dir)
        if files:
            newest = max(os.path.getmtime(os.path.join(backup_dir, f)) for f in files)
            result['newest_backup_min'] = round((time.time() - newest) / 60, 1)
            result['backup_count'] = len(files)
    except Exception:
        pass

    return result


def collect_tjai():
    """Collect tjai application stats."""
    now = time.time()
    metrics = {}

    # Entry counts by kind
    kind_counts = dict(
        Entry.objects.filter(deleted_at__isnull=True)
        .values_list('kind').annotate(c=Count('id'))
    )
    metrics['entries_by_kind'] = kind_counts
    metrics['entries_active'] = sum(kind_counts.values())
    metrics['entries_deleted'] = Entry.objects.filter(deleted_at__isnull=False).count()
    metrics['entries_total'] = metrics['entries_active'] + metrics['entries_deleted']

    # Contexts and tags
    metrics['contexts'] = Context.objects.count()
    metrics['unique_tags'] = TagStats.objects.count()

    # Recent activity
    metrics['modified_24h'] = Entry.objects.filter(
        deleted_at__isnull=True, timestamp_modified__gte=now - 86400
    ).count()
    metrics['modified_1h'] = Entry.objects.filter(
        deleted_at__isnull=True, timestamp_modified__gte=now - 3600
    ).count()

    # Machines
    machines = []
    for m in Machine.objects.filter(is_active=1):
        name = m.hostname or m.machine_id[:8]
        age_min = int((now - m.last_sync) / 60) if m.last_sync else None
        machines.append({'name': name, 'last_sync_min': age_min})
    machines.sort(key=lambda x: x['name'])
    metrics['machines'] = machines

    # Agents
    metrics['agents'] = _collect_agents(now)

    # Action entries
    actions = list(Entry.objects.filter(
        kind='action', deleted_at__isnull=True
    ).exclude(status='done'))
    # Bulk-fetch agent sysconfig keys
    agent_sysconfig = {}
    for sc in SysConfig.objects.filter(key__startswith='agent_'):
        agent_sysconfig[sc.key] = sc.value
    metrics['actions'] = []
    for a in actions:
        data = a.data or {}
        last_run = data.get('last_run')
        action_id = data.get('entry_id')
        action_info = {
            'id': str(a.id),
            'content': a.content[:60],
            'trigger': data.get('trigger', '?'),
            'interval_h': data.get('interval_hours', 24),
            'last_run_min': round((now - last_run) / 60, 1) if last_run else None,
        }
        if action_id:
            agent_status = agent_sysconfig.get(f'agent_{action_id}_status')
            launched = agent_sysconfig.get(f'agent_{action_id}_launched')
            completed = agent_sysconfig.get(f'agent_{action_id}_completed')
            tracking = agent_sysconfig.get(f'agent_{action_id}_tracking')
            action_info['agent_status'] = agent_status
            if launched:
                action_info['agent_launched_min'] = round(
                    (now - float(launched)) / 60, 1)
            if completed:
                action_info['agent_completed_min'] = round(
                    (now - float(completed)) / 60, 1)
            if launched and completed:
                try:
                    action_info['agent_duration_min'] = round(
                        (float(completed) - float(launched)) / 60, 1)
                except (ValueError, TypeError):
                    pass
            if tracking:
                action_info['agent_tracking'] = tracking
        metrics['actions'].append(action_info)

    # Log entries count
    metrics['log_entries'] = AppLog.objects.count()

    return metrics


def assess_health(system, postgres, tjai=None, dropbox=None, backups=None):
    """Determine health status from metrics."""
    issues = []
    cpu_count = system.get('cpu_count', 1)
    load = system.get('load_5m', 0)  # 5m avg — less noisy than 1m
    mem_avail = system.get('mem_available_pct', 100)
    disk = system.get('disk_used_pct', 0)
    swap = system.get('swap_used_pct', 0)
    cache_hit = postgres.get('cache_hit_ratio', 100)

    # RED thresholds
    if load > 3 * cpu_count:
        issues.append(('red', f'Load {load} > 3x CPUs ({cpu_count})'))
    if mem_avail < 10:
        issues.append(('red', f'Memory available {mem_avail}% < 10%'))
    if disk > 90:
        issues.append(('red', f'Disk {disk}% > 90%'))
    if cache_hit < 95:
        issues.append(('red', f'PG cache hit {cache_hit}% < 95%'))
    if swap > 50:
        issues.append(('red', f'Swap {swap}% > 50%'))

    # YELLOW thresholds (skip if already red for same metric)
    red_prefixes = {i[1].split()[0] for i in issues}
    if 'Load' not in red_prefixes and load > 2 * cpu_count:
        issues.append(('yellow', f'Load {load} > 2x CPUs ({cpu_count})'))
    if 'Memory' not in red_prefixes and mem_avail < 20:
        issues.append(('yellow', f'Memory available {mem_avail}% < 20%'))
    if 'Disk' not in red_prefixes and disk > 80:
        issues.append(('yellow', f'Disk {disk}% > 80%'))
    if 'PG' not in red_prefixes and cache_hit < 99:
        issues.append(('yellow', f'PG cache hit {cache_hit}% < 99%'))
    if 'Swap' not in red_prefixes and swap > 20:
        issues.append(('yellow', f'Swap {swap}% > 20%'))

    # Agent health
    if tjai:
        for agent in tjai.get('agents', []):
            name = agent.get('name', '?')
            st = agent.get('status', 'down')
            if st == 'down':
                issues.append(('red', f'{name} is down'))
            elif st == 'stale':
                issues.append(('yellow', f'{name} heartbeat stale'))

    # Dropbox health
    if dropbox:
        if not dropbox.get('running'):
            if dropbox.get('restarted'):
                issues.append(('yellow', 'Dropbox was down, restart attempted'))
            else:
                issues.append(('red', 'Dropbox is down'))

    # Backup health
    if backups:
        latest = backups.get('latest')
        if not backups.get('exists') or not latest:
            issues.append(('red', 'No backups found'))
        else:
            from datetime import datetime, timedelta
            try:
                latest_date = datetime.strptime(latest['date'], '%Y-%m-%d').date()
                age_days = (datetime.now().date() - latest_date).days
                if age_days > 2:
                    issues.append(('red', f'Latest backup is {age_days} days old'))
                elif age_days > 1:
                    issues.append(('yellow', f'Latest backup is {age_days} days old'))
            except ValueError:
                pass
            if not latest.get('all_present'):
                missing = [k for k, v in latest.get('files', {}).items() if v is None]
                if missing:
                    issues.append(('yellow', f'Backup missing: {", ".join(missing)}'))
            db_mb = latest.get('db_size_mb')
            if db_mb is not None and db_mb < 1:
                issues.append(('red', f'Backup DB dump too small ({db_mb} MB)'))

    if any(level == 'red' for level, _ in issues):
        status = 'red'
    elif any(level == 'yellow' for level, _ in issues):
        status = 'yellow'
    else:
        status = 'green'

    return status, [msg for _, msg in issues]


def main():
    print("Collecting system metrics...")
    system = collect_system()

    print("Collecting PostgreSQL metrics...")
    postgres = collect_postgres()

    print("Collecting process info...")
    processes = collect_processes()

    print("Collecting CloudWatch metrics...")
    cloudwatch = collect_cloudwatch()

    print("Collecting Dropbox status...")
    dropbox = collect_dropbox()

    print("Collecting backup status...")
    backups = collect_backups()

    print("Collecting tjai stats...")
    tjai = collect_tjai()

    status, issues = assess_health(system, postgres, tjai, dropbox, backups)

    health_data = {
        'timestamp': time.time(),
        'status': status,
        'issues': issues,
        'recheck': dropbox.get('restarted', False),
        'system': system,
        'postgres': postgres,
        'processes': processes,
        'cloudwatch': cloudwatch,
        'dropbox': dropbox,
        'backups': backups,
        'tjai': tjai,
    }

    now = time.time()
    SysConfig.objects.update_or_create(
        key='system_health_status',
        defaults={'value': status, 'timestamp_modified': now},
    )
    SysConfig.objects.update_or_create(
        key='system_health_data',
        defaults={'value': json.dumps(health_data), 'timestamp_modified': now},
    )

    print(f"Health: {status.upper()}")
    if issues:
        for issue in issues:
            print(f"  - {issue}")
    print("Written to sysconfig.")


if __name__ == '__main__':
    main()
