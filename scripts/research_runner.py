#!/usr/bin/env python3
"""Research queue runner — serial processor for :research tagged entries.

Mechanical harness that walks the research queue and dispatches Claude
for each topic. Manages its own sysconfig status for mutual exclusion
and real-time tracking on the Research and System pages.

Usage:
    python research_runner.py              # process queue
    python research_runner.py --dry-run    # show what would run
"""
import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time

import bootstrap  # noqa: F401 - Django setup

from tjai_app.models import Entry, SysConfig, Tag
from tjai_app.action_runner import logger

STALE_TIMEOUT = 1800  # 30 minutes — treat running status as stale after this
ACTION_ID = 'research-agent'


def sysconfig_set(key, value):
    """Write a sysconfig key."""
    now = time.time()
    SysConfig.objects.update_or_create(
        key=key, defaults={'value': str(value), 'timestamp_modified': now})


def sysconfig_get(key):
    """Read a sysconfig key, returns None if missing."""
    try:
        return SysConfig.objects.get(key=key).value
    except SysConfig.DoesNotExist:
        return None


def check_lock():
    """Check mutual exclusion. Returns True if safe to proceed."""
    status = sysconfig_get(f'agent_{ACTION_ID}_status')
    if status == 'running':
        launched = sysconfig_get(f'agent_{ACTION_ID}_launched')
        if launched:
            elapsed = time.time() - float(launched)
            if elapsed < STALE_TIMEOUT:
                logger.info("Research agent already running (%.0fm), exiting", elapsed / 60)
                return False
            else:
                logger.warning("Research agent stale (%.0fm), taking over", elapsed / 60)
    return True


def claim_lock():
    """Claim the execution lock."""
    sysconfig_set(f'agent_{ACTION_ID}_status', 'running')
    sysconfig_set(f'agent_{ACTION_ID}_launched', time.time())


def release_lock(status='completed'):
    """Release the execution lock."""
    now = time.time()
    sysconfig_set(f'agent_{ACTION_ID}_status', status)
    if status == 'completed':
        sysconfig_set(f'agent_{ACTION_ID}_completed', now)


def get_pending_items():
    """Find pending research entries, sorted by priority then FIFO."""
    research_ids = Tag.objects.filter(
        tag_name='research'
    ).values_list('entry_id', flat=True)
    entries = Entry.objects.filter(
        id__in=research_ids,
        kind='memory',
        deleted_at__isnull=True,
    ).exclude(status='done').order_by('priority', 'timestamp_created')
    return list(entries)


def get_system_prompt():
    """Load the research system prompt from tjai."""
    entry = Entry.objects.filter(
        data__entry_id='research-system-prompt',
        deleted_at__isnull=True,
    ).first()
    if not entry:
        logger.error("research-system-prompt entry not found")
        return None
    return entry.content


def build_task_prompt(entry):
    """Build the per-topic task prompt."""
    data = entry.data or {}
    entry_id = data.get('entry_id', str(entry.id)[:8])
    return (
        f"Research topic: {entry.content}\n\n"
        f"Entry UUID: {entry.id}\n"
        f"Entry ID: {entry_id}\n\n"
        f"Write your completed research report directly into this entry "
        f"using edit_entry(entry_id=\"{entry.id}\", content=<report>, status=\"done\"). "
        f"Preserve the original topic as the first line, then add the full report below it."
    )


def run_research(entry, system_prompt, dry_run=False):
    """Run Claude on a single research entry. Returns True on success."""
    data = entry.data or {}
    entry_id = data.get('entry_id', str(entry.id)[:8])
    logger.info("Research: %s — %s", entry_id, entry.content[:60])

    if dry_run:
        logger.info("  [dry-run] would dispatch claude")
        return True

    claude_path = shutil.which('claude')
    if not claude_path:
        logger.error("'claude' CLI not found in PATH")
        return False

    task_prompt = build_task_prompt(entry)
    cmd = [
        claude_path,
        '-p', task_prompt,
        '--system-prompt', system_prompt,
        '--output-format', 'text',
        '--model', 'opus',
    ]

    env = os.environ.copy()
    env.pop('CLAUDECODE', None)

    # Update sysconfig with current item
    sysconfig_set(f'agent_{ACTION_ID}_entry', str(entry.id))

    logger.info("  Dispatching claude (opus)...")
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)

    if result.returncode != 0:
        logger.error("  Claude failed (exit %d)", result.returncode)
        if result.stderr:
            for line in result.stderr.strip().split('\n')[:10]:
                logger.error("    %s", line)
        return False

    logger.info("  Done")
    return True


def find_by_entry_id(entry_id):
    """Look up a research entry by its human-readable entry_id."""
    entry = Entry.objects.filter(
        data__entry_id=entry_id,
        kind='memory',
        deleted_at__isnull=True,
    ).first()
    return entry


def main():
    parser = argparse.ArgumentParser(description='Research queue runner')
    parser.add_argument('--run', type=str, metavar='ENTRY_ID',
                        help='Run a specific entry by entry_id (e.g. research-mcp)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would run without executing')
    args = parser.parse_args()

    if not args.dry_run:
        if not check_lock():
            return
        claim_lock()

    try:
        system_prompt = get_system_prompt()
        if not system_prompt:
            if not args.dry_run:
                release_lock('failed')
            return

        if args.run:
            entry = find_by_entry_id(args.run)
            if not entry:
                logger.error("Entry not found: %s", args.run)
                if not args.dry_run:
                    release_lock('failed')
                return
            items = [entry]
        else:
            items = get_pending_items()

        if not items:
            logger.info("Research queue empty")
            if not args.dry_run:
                release_lock('completed')
            return

        logger.info("Research queue: %d item(s)", len(items))

        # Process first batch, then re-check for new items after each completion
        while items:
            entry = items[0]
            success = run_research(entry, system_prompt, dry_run=args.dry_run)
            if not success and not args.dry_run:
                logger.error("Research failed, continuing to next item")
            # Re-query queue for new items added during processing
            items = get_pending_items()

        if not args.dry_run:
            release_lock('completed')

        logger.info("Research runner finished")
    except Exception:
        if not args.dry_run:
            release_lock('failed')
        raise


if __name__ == '__main__':
    main()
