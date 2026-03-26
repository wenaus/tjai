#!/usr/bin/env python3
"""Watchdog for tjai action agent.

Detects anomalies: loop behavior, stale agents, zombie processes,
error storms, heartbeat failures, and resource exhaustion.

Runs as a mechanical_script via the action agent scheduler (interval: 10 min).
Logs only non-OK results to AppLog; stores full results in SysConfig.
On anomaly: sends email via SES and triggers watchdog-escalation action.
"""
import json
import logging
import os
import signal
import sys
import time
from datetime import timedelta

import bootstrap  # noqa: F401 - Django setup

from django.db import connection
from django.utils import timezone

from tjai_app.db_log_handler import DbLogHandler
from tjai_app.models import AppLog, Entry, SysConfig

logger = logging.getLogger('watchdog')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='watchdog')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)

# ── Thresholds ──────────────────────────────────────────────────────────
LOOP_WINDOW_HOURS = 2
LOOP_MIN_DISPATCHES = 3
STALE_AGENT_HOURS = 2
ERROR_STORM_WINDOW_MIN = 10
ERROR_STORM_THRESHOLD = 20
HEARTBEAT_STALE_MIN = 10
MEM_AVAIL_CRIT_PCT = 10
SWAP_USED_CRIT_PCT = 50
DISK_USED_CRIT_PCT = 90

MAX_EMAILS_PER_ESCALATION = 1
EMAIL_FROM = 'wenaus@gmail.com'
EMAIL_TO = 'wenaus@gmail.com'
SES_REGION = 'us-east-1'


# ── Checks ──────────────────────────────────────────────────────────────

def check_loop_detection():
    """Detect same entry dispatched multiple times within window."""
    cutoff = timezone.now() - timedelta(hours=LOOP_WINDOW_HOURS)
    with connection.cursor() as cur:
        cur.execute("""
            SELECT extra_data->>'entry_id' AS eid, COUNT(*) AS cnt
            FROM applog
            WHERE source = 'agent_complete'
              AND timestamp >= %s
              AND extra_data->>'entry_id' IS NOT NULL
            GROUP BY eid
            HAVING COUNT(*) >= %s
        """, [cutoff, LOOP_MIN_DISPATCHES])
        loops = cur.fetchall()

    if loops:
        details = [f"{eid}: {cnt}x" for eid, cnt in loops]
        return {
            'check': 'loop_detection',
            'status': 'critical',
            'detail': f"Loop detected: {'; '.join(details)}",
        }
    return {'check': 'loop_detection', 'status': 'ok', 'detail': ''}


def check_stale_agents():
    """Check for agents stuck in 'running' state with no recent activity."""
    now = time.time()
    threshold_sec = STALE_AGENT_HOURS * 3600
    stale = []

    for sc in SysConfig.objects.filter(
        key__startswith='agent_', key__endswith='_status',
    ):
        if sc.value != 'running':
            continue
        # Extract action_id: agent_{action_id}_status
        action_id = sc.key[6:-7]
        if not action_id:
            continue

        launched = SysConfig.objects.filter(
            key=f'agent_{action_id}_launched'
        ).values_list('value', flat=True).first()
        if not launched:
            continue

        age = now - float(launched)
        if age < threshold_sec:
            continue

        last_activity = SysConfig.objects.filter(
            key=f'agent_{action_id}_last_activity'
        ).values_list('value', flat=True).first()
        activity_age = (now - float(last_activity)) if last_activity else age

        if activity_age > threshold_sec:
            stale.append(
                f"{action_id}: running {age / 3600:.1f}h, "
                f"last activity {activity_age / 3600:.1f}h ago"
            )

    if stale:
        return {
            'check': 'stale_agents',
            'status': 'warning',
            'detail': '; '.join(stale),
        }
    return {'check': 'stale_agents', 'status': 'ok', 'detail': ''}


def check_zombie_processes():
    """Find claude processes in /proc not matching any running sysconfig state."""
    claude_pids = []
    for pid_dir in os.listdir('/proc'):
        if not pid_dir.isdigit():
            continue
        try:
            with open(f'/proc/{pid_dir}/cmdline', 'rb') as f:
                cmdline = f.read().decode('utf-8', errors='replace')
            if ('claude' in cmdline and '--output-format' in cmdline
                    and 'text' in cmdline):
                claude_pids.append(int(pid_dir))
        except (PermissionError, FileNotFoundError):
            continue

    if not claude_pids:
        return {'check': 'zombie_processes', 'status': 'ok', 'detail': ''}

    has_running = SysConfig.objects.filter(
        key__startswith='agent_', key__endswith='_status', value='running',
    ).exists()

    if not has_running:
        return {
            'check': 'zombie_processes',
            'status': 'warning',
            'detail': (f"{len(claude_pids)} claude process(es) with no "
                       f"running agent: PIDs {claude_pids}"),
        }
    return {'check': 'zombie_processes', 'status': 'ok', 'detail': ''}


def check_entry_flood(window=1800, threshold=5):
    """Detect rapid creation of similar entries via Jaccard similarity.

    Clusters entries created in the last `window` seconds by word-set
    similarity (Jaccard > 0.7). If any cluster has `threshold`+ members,
    that's a flood — likely a runaway dispatch.
    """
    now = time.time()
    cutoff = now - window
    recent = Entry.objects.filter(
        timestamp_created__gte=cutoff,
        deleted_at__isnull=True,
    ).values_list('content', flat=True)

    word_sets = []
    for content in recent:
        if content:
            word_sets.append(set(content[:200].lower().split()))

    if len(word_sets) < threshold:
        return {'check': 'entry_flood', 'status': 'ok', 'detail': ''}

    # Cluster by greedy Jaccard > 0.7
    clusters = []
    for ws in word_sets:
        placed = False
        for cluster in clusters:
            intersection = ws & cluster[0]
            union = ws | cluster[0]
            if union and len(intersection) / len(union) > 0.7:
                cluster[1] += 1
                placed = True
                break
        if not placed:
            clusters.append([ws, 1])

    floods = [c for c in clusters if c[1] >= threshold]
    if floods:
        worst = max(floods, key=lambda c: c[1])
        count = worst[1]
        sample = ' '.join(sorted(worst[0]))[:80]
        return {
            'check': 'entry_flood',
            'status': 'critical',
            'detail': f"{count} similar entries in {window // 60}min (words: {sample})",
        }
    return {'check': 'entry_flood', 'status': 'ok', 'detail': ''}


def check_error_storm():
    """Check for excessive errors in AppLog in the last N minutes."""
    cutoff = timezone.now() - timedelta(minutes=ERROR_STORM_WINDOW_MIN)
    error_count = AppLog.objects.filter(
        timestamp__gte=cutoff,
        level__gte=logging.ERROR,
    ).count()

    if error_count >= ERROR_STORM_THRESHOLD:
        return {
            'check': 'error_storm',
            'status': 'critical',
            'detail': f"{error_count} errors in last {ERROR_STORM_WINDOW_MIN} min",
        }
    return {'check': 'error_storm', 'status': 'ok', 'detail': ''}


def check_heartbeat():
    """Check if action_agent_heartbeat is recent."""
    hb = SysConfig.objects.filter(
        key='action_agent_heartbeat'
    ).values_list('value', flat=True).first()

    if not hb:
        return {
            'check': 'heartbeat',
            'status': 'critical',
            'detail': 'No heartbeat found',
        }

    age_min = (time.time() - float(hb)) / 60
    if age_min > HEARTBEAT_STALE_MIN:
        return {
            'check': 'heartbeat',
            'status': 'critical',
            'detail': f'Heartbeat stale: {age_min:.0f} min ago',
        }
    return {'check': 'heartbeat', 'status': 'ok', 'detail': ''}


def check_resources():
    """Check memory, swap, and disk thresholds."""
    issues = []

    # Memory from /proc/meminfo
    meminfo = {}
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                kv = line.split(':')
                if len(kv) == 2:
                    meminfo[kv[0].strip()] = int(kv[1].strip().split()[0])
    except (OSError, ValueError):
        pass

    total = meminfo.get('MemTotal', 0)
    available = meminfo.get('MemAvailable', 0)
    if total:
        mem_pct = available / total * 100
        if mem_pct < MEM_AVAIL_CRIT_PCT:
            issues.append(f'Memory available {mem_pct:.0f}%')

    swap_total = meminfo.get('SwapTotal', 0)
    swap_free = meminfo.get('SwapFree', 0)
    if swap_total:
        swap_pct = (swap_total - swap_free) / swap_total * 100
        if swap_pct > SWAP_USED_CRIT_PCT:
            issues.append(f'Swap used {swap_pct:.0f}%')

    # Disk
    try:
        st = os.statvfs('/')
        disk_total = st.f_frsize * st.f_blocks
        disk_used = st.f_frsize * (st.f_blocks - st.f_bfree)
        if disk_total:
            disk_pct = disk_used / disk_total * 100
            if disk_pct > DISK_USED_CRIT_PCT:
                issues.append(f'Disk used {disk_pct:.0f}%')
    except OSError:
        pass

    if issues:
        return {
            'check': 'resources',
            'status': 'critical',
            'detail': '; '.join(issues),
        }
    return {'check': 'resources', 'status': 'ok', 'detail': ''}


# ── Email ───────────────────────────────────────────────────────────────

def send_watchdog_email(subject, body):
    """Send alert email via AWS SES."""
    import boto3
    ses = boto3.client('ses', region_name=SES_REGION)
    ses.send_email(
        Source=EMAIL_FROM,
        Destination={'ToAddresses': [EMAIL_TO]},
        Message={
            'Subject': {'Data': subject, 'Charset': 'UTF-8'},
            'Body': {'Text': {'Data': body, 'Charset': 'UTF-8'}},
        },
    )


# ── Escalation ──────────────────────────────────────────────────────────

def _wake_agent():
    """Send SIGHUP to action agent daemon."""
    pid_str = SysConfig.objects.filter(
        key='action_agent_pid'
    ).values_list('value', flat=True).first()
    if pid_str and pid_str.isdigit():
        try:
            os.kill(int(pid_str), signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass


def trigger_escalation(non_ok_results, worst_status):
    """Send email (with cap) and trigger watchdog-escalation action."""
    # Check mutex: is escalation already running?
    esc_status = SysConfig.objects.filter(
        key='agent_watchdog-escalation_status'
    ).values_list('value', flat=True).first()
    if esc_status == 'running':
        logger.info("Escalation already running, skipping")
        return

    # Load the escalation action entry
    esc_action = Entry.objects.filter(
        data__entry_id='watchdog-escalation',
        kind='action',
        deleted_at__isnull=True,
    ).first()
    if not esc_action:
        logger.error("watchdog-escalation action entry not found")
        return

    esc_data = esc_action.data or {}

    # Build anomaly summary
    level = worst_status.upper()
    summary_lines = [f"tjai watchdog detected {len(non_ok_results)} anomaly(ies):\n"]
    for r in non_ok_results:
        summary_lines.append(
            f"  [{r['status'].upper()}] {r['check']}: {r['detail']}"
        )
    summary_lines.append(f"\nTimestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    summary = '\n'.join(summary_lines)

    # Email — respect cap
    emails_sent = esc_data.get('emails_sent', 0)
    max_emails = esc_data.get('max_emails', MAX_EMAILS_PER_ESCALATION)
    if emails_sent < max_emails:
        subject = f"[tjai watchdog] {level}: {non_ok_results[0]['detail'][:60]}"
        try:
            send_watchdog_email(subject, summary)
            logger.info("Watchdog alert email sent: %s", subject)
            esc_data['emails_sent'] = emails_sent + 1
        except Exception as e:
            logger.error("Failed to send watchdog email: %s", e)
    else:
        logger.info("Email cap reached (%d/%d), skipping email",
                     emails_sent, max_emails)

    # Set target and trigger
    esc_data['next_target'] = f"WATCHDOG ALERT:\n{summary}"
    esc_data['last_run'] = 0
    esc_action.data = esc_data
    esc_action.timestamp_modified = time.time()
    esc_action.save(update_fields=['data', 'timestamp_modified'])

    # Wake action agent
    SysConfig.objects.update_or_create(
        key='action_agent_wake_requested',
        defaults={'value': '1', 'timestamp_modified': time.time()})
    _wake_agent()
    logger.info("Watchdog escalation triggered")


# ── Main ────────────────────────────────────────────────────────────────

def main():
    """Run all watchdog checks and handle results."""
    results = [
        check_loop_detection(),
        check_stale_agents(),
        check_zombie_processes(),
        check_error_storm(),
        check_entry_flood(),
        check_heartbeat(),
        check_resources(),
    ]

    non_ok = [r for r in results if r['status'] != 'ok']

    # Log only non-OK results (don't flood AppLog)
    for r in non_ok:
        log_level = (logging.ERROR if r['status'] == 'critical'
                     else logging.WARNING)
        logger.log(log_level, "[%s] %s: %s",
                   r['status'].upper(), r['check'], r['detail'])

    # Determine worst status
    worst = 'ok'
    if any(r['status'] == 'critical' for r in results):
        worst = 'critical'
    elif any(r['status'] == 'warning' for r in results):
        worst = 'warning'

    # Store results in SysConfig
    now = time.time()
    SysConfig.objects.update_or_create(
        key='watchdog_last_results',
        defaults={'value': json.dumps(results), 'timestamp_modified': now})
    SysConfig.objects.update_or_create(
        key='watchdog_last_run',
        defaults={'value': str(now), 'timestamp_modified': now})
    SysConfig.objects.update_or_create(
        key='watchdog_status',
        defaults={'value': worst, 'timestamp_modified': now})

    if non_ok:
        trigger_escalation(non_ok, worst)
    else:
        # All clear — reset email counter on the escalation action
        esc_action = Entry.objects.filter(
            data__entry_id='watchdog-escalation',
            kind='action',
            deleted_at__isnull=True,
        ).first()
        if esc_action:
            esc_data = esc_action.data or {}
            if esc_data.get('emails_sent', 0) > 0:
                esc_data['emails_sent'] = 0
                esc_action.data = esc_data
                esc_action.save(update_fields=['data'])


if __name__ == '__main__':
    main()
