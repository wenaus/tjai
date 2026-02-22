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
import traceback

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

    while time.time() < deadline:
        if shutdown_requested or wake_requested:
            break
        # Poll sysconfig for refresh requests from web UI
        elapsed = time.time() - last_sysconfig_check
        if elapsed >= SYSCONFIG_POLL_INTERVAL:
            last_sysconfig_check = time.time()
            from tjai_app.models import SysConfig
            req = SysConfig.objects.filter(
                key='system_health_refresh_requested'
            ).values_list('value', flat=True).first()
            if req:
                wake_requested = True
                break
        time.sleep(1)

    if wake_requested:
        wake_requested = False
        logger.info("Woken by SIGHUP or sysconfig request")


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

    # --run: execute one action and exit
    if args.run:
        action = Entry.objects.filter(id=args.run, kind='action',
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
            # Check for on-demand system health refresh request
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
