#!/usr/bin/env python3
"""tjai action agent — supervised always-on daemon.

Checks active action entries on schedule, executes due actions (mechanical
scripts + journal entries + AI dispatch). Managed by supervisord.

Usage:
    python action_agent.py                      # daemon mode (default)
    python action_agent.py --trigger overnight   # daemon, only overnight actions
    python action_agent.py --run <entry_id>      # execute one action, then exit
    python action_agent.py --dry-run             # show what would run, then exit
"""
import argparse
import os
import signal
import sys
import time

import bootstrap  # noqa: F401 - Django setup
from tjai_app.action_runner import (
    get_due_actions, execute_action, write_heartbeat, logger,
)
from tjai_app.models import Entry

MAX_SLEEP = 300   # 5 minutes
MIN_SLEEP = 30    # 30 seconds

shutdown_requested = False
wake_requested = False


def handle_shutdown(signum, frame):
    global shutdown_requested
    shutdown_requested = True


def handle_wake(signum, frame):
    global wake_requested
    wake_requested = True


SYSCONFIG_POLL_INTERVAL = 3  # seconds between sysconfig checks


def sleep_until_next(trigger_filter=None):
    """Sleep until the next action is due, capped at MAX_SLEEP.

    Polls wake_requested every second (SIGHUP) and sysconfig every few
    seconds (web UI refresh requests) to break sleep promptly.
    """
    global wake_requested

    actions = Entry.objects.filter(
        kind='action',
        deleted_at__isnull=True,
    ).exclude(status='done').exclude(status='blocked')

    now = time.time()
    earliest_due = now + MAX_SLEEP

    for action in actions:
        data = action.data or {}
        if trigger_filter and data.get('trigger') != trigger_filter:
            continue
        last_run = data.get('last_run', 0)
        interval_hours = data.get('interval_hours', 24)
        next_due = last_run + interval_hours * 3600
        if next_due < earliest_due:
            earliest_due = next_due

    sleep_secs = max(MIN_SLEEP, min(MAX_SLEEP, earliest_due - now))
    deadline = now + sleep_secs
    last_sysconfig_check = 0
    last_health_check = 0

    while time.time() < deadline:
        if shutdown_requested or wake_requested:
            break
        now_loop = time.time()
        # Poll sysconfig for refresh requests from web UI
        if now_loop - last_sysconfig_check >= SYSCONFIG_POLL_INTERVAL:
            last_sysconfig_check = now_loop
            from tjai_app.models import SysConfig
            # Check for kill request first (runs as admin, has permission)
            _check_kill_request()
            for check_key in ('action_agent_wake_requested', 'system_health_refresh_requested'):
                req = SysConfig.objects.filter(
                    key=check_key
                ).values_list('value', flat=True).first()
                if req:
                    wake_requested = True
                    break
            if wake_requested:
                break
        # Periodic agent health check
        if now_loop - last_health_check >= HEALTH_CHECK_INTERVAL:
            last_health_check = now_loop
            try:
                _check_agent_health()
            except Exception as e:
                logger.error("Agent health check failed: %s", e)
        time.sleep(1)

    if wake_requested:
        wake_requested = False
        logger.info("Woken by SIGHUP or sysconfig request")
        # Clear the web-UI wake flag so we don't re-trigger
        from tjai_app.models import SysConfig
        SysConfig.objects.filter(key='action_agent_wake_requested').update(
            value='', timestamp_modified=time.time())


def _check_kill_request():
    """Kill zombie claude agent processes if requested via sysconfig flag."""
    from tjai_app.models import SysConfig
    req = SysConfig.objects.filter(key='agent_kill_requested').first()
    if not req or not req.value:
        return
    req.value = ''
    req.timestamp_modified = time.time()
    req.save(update_fields=['value', 'timestamp_modified'])
    logger.info("Kill requested — scanning for zombie agent processes")
    killed = 0
    import subprocess as sp
    try:
        result = sp.run(['ps', '-eo', 'pid=,etimes=,args='],
                        capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, sp.TimeoutExpired):
        return
    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        if '--output-format' not in line or 'text' not in line:
            continue
        if 'claude' not in line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            logger.warning("Killed zombie claude process PID %d", pid)
            killed += 1
        except ProcessLookupError:
            pass
    logger.info("Kill request completed: %d process(es) killed", killed)


HEALTH_CHECK_INTERVAL = 30  # seconds between agent health checks


def _check_agent_health():
    """Probe health of all running agents using tracking entry activity + /proc.

    For each agent with sysconfig status='running':
    1. Read tracking entry UUID from agent_{id}_tracking
    2. Check tracking entry timestamp_modified (natural heartbeat from MCP tool calls)
    3. Check process liveness via /proc scan
    4. Write findings to sysconfig for UI consumption
    """
    from tjai_app.models import SysConfig, Entry

    now = time.time()

    # Find all agents with status='running'
    running_agents = []
    for sc in SysConfig.objects.filter(key__endswith='_status', key__startswith='agent_'):
        if sc.value != 'running':
            continue
        # Extract action_id: agent_{action_id}_status
        parts = sc.key.split('_', 1)  # ['agent', '{action_id}_status']
        if len(parts) < 2:
            continue
        action_id = parts[1].rsplit('_status', 1)[0]
        if not action_id or action_id == 'agent':
            continue
        running_agents.append(action_id)

    if not running_agents:
        return

    # Check which claude agent processes are alive (single /proc scan for all)
    alive_pids = _scan_agent_processes()

    for action_id in running_agents:
        tracking_uuid = SysConfig.objects.filter(
            key=f'agent_{action_id}_tracking'
        ).values_list('value', flat=True).first()

        last_activity = None
        if tracking_uuid:
            tracking_entry = Entry.objects.filter(
                id=tracking_uuid, deleted_at__isnull=True,
            ).values_list('timestamp_modified', flat=True).first()
            if tracking_entry:
                last_activity = float(tracking_entry)

        process_alive = len(alive_pids) > 0

        # Determine health
        activity_age = (now - last_activity) if last_activity else None
        if activity_age is not None and activity_age < 120:
            health = 'active'
        elif activity_age is not None and activity_age < 600:
            health = 'idle'
        else:
            health = 'stale'

        # Write findings to sysconfig
        updates = {
            f'agent_{action_id}_process_alive': '1' if process_alive else '0',
            f'agent_{action_id}_health': health,
        }
        if last_activity is not None:
            updates[f'agent_{action_id}_last_activity'] = str(last_activity)

        for key, value in updates.items():
            SysConfig.objects.update_or_create(
                key=key, defaults={'value': value, 'timestamp_modified': now})

        # Recovery: stale + no process = dead agent, reset status
        if health == 'stale' and not process_alive:
            logger.warning("%s stale (no activity %.0fm, no process) — resetting to idle",
                           action_id,
                           activity_age / 60 if activity_age else 0)
            SysConfig.objects.update_or_create(
                key=f'agent_{action_id}_status',
                defaults={'value': 'idle', 'timestamp_modified': now})
            SysConfig.objects.update_or_create(
                key=f'agent_{action_id}_last_error',
                defaults={'value': 'Agent became stale and process died',
                          'timestamp_modified': now})
            SysConfig.objects.update_or_create(
                key=f'agent_{action_id}_last_error_time',
                defaults={'value': str(now), 'timestamp_modified': now})
        elif health == 'stale' and process_alive:
            # Process alive but idle > 10min — log warning, give one more cycle
            logger.warning("%s stale but process alive (activity %.0fm ago) — monitoring",
                           action_id,
                           activity_age / 60 if activity_age else 0)


def _scan_agent_processes():
    """Scan /proc for claude agent processes (--output-format text).

    Returns list of PIDs.
    """
    pids = []
    for pid_dir in os.listdir('/proc'):
        if not pid_dir.isdigit():
            continue
        try:
            with open(f'/proc/{pid_dir}/cmdline', 'rb') as f:
                cmdline = f.read().decode('utf-8', errors='replace')
            if 'claude' in cmdline and '--output-format' in cmdline and 'text' in cmdline:
                pids.append(int(pid_dir))
        except (PermissionError, FileNotFoundError):
            continue
    return pids


def _check_health_refresh():
    """Run system health collection if requested via sysconfig flag."""
    from tjai_app.models import SysConfig
    req = SysConfig.objects.filter(key='system_health_refresh_requested').first()
    if not req or not req.value:
        return
    # Clear the flag first to avoid re-runs
    req.value = ''
    req.timestamp_modified = time.time()
    req.save(update_fields=['value', 'timestamp_modified'])
    logger.info("Running system health refresh (requested)")
    try:
        from system_health import main as health_main
        health_main()
    except Exception as e:
        logger.error("System health refresh failed: %s", e, exc_info=True)


def main():
    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGQUIT, handle_shutdown)
    signal.signal(signal.SIGHUP, handle_wake)

    parser = argparse.ArgumentParser(description='tjai action agent')
    parser.add_argument('--trigger', help='Only run actions with this trigger type')
    parser.add_argument('--dry-run', action='store_true', help='Show what would run')
    parser.add_argument('--run', help='Execute a single action by entry ID, then exit')
    args = parser.parse_args()

    # --dry-run: show due actions and exit
    if args.dry_run:
        due = get_due_actions(trigger_filter=args.trigger)
        if not due:
            logger.info("No actions due.")
        else:
            for action in due:
                data = action.data or {}
                logger.info("  DUE: %s  [trigger=%s, interval=%sh]",
                            action.content[:80], data.get('trigger', '?'),
                            data.get('interval_hours', 24))
        return

    # --run: execute one action and exit (accepts UUID or data.entry_id)
    if args.run:
        action = Entry.objects.filter(id=args.run, kind='action',
                                      deleted_at__isnull=True).first()
        if not action:
            action = Entry.objects.filter(
                data__entry_id=args.run, kind='action',
                deleted_at__isnull=True).first()
        if not action:
            logger.error("Action not found: %s", args.run)
            sys.exit(1)
        execute_action(action)
        return

    # Daemon mode (default)
    pid = os.getpid()
    from tjai_app.models import SysConfig
    SysConfig.objects.update_or_create(
        key='action_agent_pid',
        defaults={'value': str(pid), 'timestamp_modified': time.time()},
    )
    logger.info("Action agent started (PID %d)", pid)
    while not shutdown_requested:
        try:
            _check_health_refresh()

            due = get_due_actions(trigger_filter=args.trigger)
            for action in due:
                if shutdown_requested:
                    break
                try:
                    execute_action(action)
                except Exception as e:
                    logger.error("Action %s failed: %s", action.id, e,
                                 exc_info=True)
            write_heartbeat()
            sleep_until_next(trigger_filter=args.trigger)
        except Exception as e:
            logger.error("Main loop: %s", e, exc_info=True)
            time.sleep(60)

    logger.info("Action agent shutting down")


if __name__ == '__main__':
    main()
