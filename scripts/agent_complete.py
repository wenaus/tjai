#!/usr/bin/env python3
"""Mark an agent action as completed/failed in sysconfig, and handle queue drain.

Called automatically after a tj agent claude process finishes.
Usage: agent_complete.py <action_entry_id> [exit_code] [stderr_file]

On timeout (exit 124), checks for live subagent reports. If subagents are
still producing work, waits for them (up to HARD_KILL_HOURS from launch).
Links subagent reports to the research entry.

All logging goes to AppLog via DbLogHandler (visible on dashboard).
"""
import os
import sys
import time
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup

from tjai_app.db_log_handler import DbLogHandler
from tjai_app.models import Entry, SysConfig, Tag

import logging

logger = logging.getLogger('agent_complete')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='agent_complete')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)

HARD_KILL_HOURS = 4
SUBAGENT_POLL_SECONDS = 300
SUBAGENT_STABLE_SECONDS = 600
MAX_RETRIES = 3
RETRY_BACKOFF_MINUTES = [15, 60, 240]  # 15min, 1h, 4h


def _count_subagent_entries(source_entry_id):
    """Count subagent report entries for a given source_entry_id."""
    return Entry.objects.filter(
        data__source_entry_id=source_entry_id,
        deleted_at__isnull=True,
    ).count()


def _link_subagent_reports(entry, source_entry_id, ref_extra):
    """Ensure all subagent report links are present in the research entry.

    Purely additive — never removes existing links. Only adds links for
    reports not already referenced in the content.
    """
    reports = list(Entry.objects.filter(
        data__source_entry_id=source_entry_id,
        deleted_at__isnull=True,
    ).order_by('timestamp_created'))

    if not reports:
        return 0

    # Find which report UUIDs are already linked in the content
    content = entry.content
    existing_uuids = {str(r.id) for r in reports if str(r.id) in content}
    new_reports = [r for r in reports if str(r.id) not in existing_uuids]

    if not new_reports and '## Subagent Reports' in content:
        # All already linked
        return len(reports)

    # Build full section with all reports (preserves order)
    links = "\n\n## Subagent Reports\n\n"
    for i, r in enumerate(reports, 1):
        size = f"{len(r.content) // 1024}K" if len(r.content) > 1024 else f"{len(r.content)} chars"
        r_data = r.data if isinstance(r.data, dict) else {}
        r_eid = r_data.get('entry_id')
        r_url = f"/tjai/entry/?entry_id={r_eid}" if r_eid else f"/tjai/entry/?uuid={r.id}"
        links += f"{i}. [Report — {size}]({r_url})\n"

    # Replace existing section or append
    if '## Subagent Reports' in content:
        content = content[:content.index('## Subagent Reports')]
    entry.content = content.rstrip() + links
    entry.save(update_fields=['content'])
    logger.info("Linked %d subagent reports (%d new) to entry %s",
                len(reports), len(new_reports), entry.id, extra=ref_extra)
    return len(reports)


def _wait_for_subagents(action_id, current_entry, ref_extra):
    """On timeout, wait for subagent reports to finish arriving.

    Returns the number of subagent reports found, or 0 if none/not applicable.
    """
    entry = Entry.objects.filter(
        id=current_entry, deleted_at__isnull=True
    ).first()
    if not entry or not isinstance(entry.data, dict):
        return 0

    source_entry_id = entry.data.get('entry_id')
    if not source_entry_id:
        return 0

    count = _count_subagent_entries(source_entry_id)
    if count == 0:
        return 0

    # There are subagent reports — check if more are still coming
    launched = SysConfig.objects.filter(
        key=f'agent_{action_id}_launched'
    ).values_list('value', flat=True).first()
    if not launched:
        return count

    hard_kill_time = float(launched) + HARD_KILL_HOURS * 3600

    # Signal that we're waiting
    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_status',
        defaults={'value': 'waiting_subagents',
                  'timestamp_modified': time.time()})
    logger.info("%s: timeout with %d subagent reports, waiting for more",
                action_id, count, extra=ref_extra)

    stable_since = time.time()
    last_count = count

    while time.time() < hard_kill_time:
        time.sleep(SUBAGENT_POLL_SECONDS)
        now = time.time()
        current_count = _count_subagent_entries(source_entry_id)

        # Update sysconfig so System page shows progress
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_subagent_count',
            defaults={'value': str(current_count),
                      'timestamp_modified': now})

        if current_count > last_count:
            last_count = current_count
            stable_since = now
            logger.info("%s: subagent count increased to %d, continuing to wait",
                        action_id, current_count, extra=ref_extra)
        elif now - stable_since > SUBAGENT_STABLE_SECONDS:
            logger.info("%s: subagent count stable at %d, all done",
                        action_id, current_count, extra=ref_extra)
            break
    else:
        logger.warning("%s: hard kill limit (%dh) reached with %d subagent reports",
                       action_id, HARD_KILL_HOURS, last_count, extra=ref_extra)

    # Link all subagent reports to the research entry
    final_count = _link_subagent_reports(entry, source_entry_id, ref_extra)
    return final_count


def _schedule_retry(action_id, ref_extra):
    """Schedule a retry with exponential backoff on agent failure.

    Sets retry_after and retry_count on the action entry's data so the
    scheduler picks it up after a delay. Returns True if retry was scheduled,
    False if max retries exceeded.
    """
    action = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id=action_id,
    ).first()
    if not action:
        logger.error("%s: action entry not found, cannot schedule retry",
                     action_id, extra=ref_extra)
        return False

    data = action.data or {}
    retry_count = data.get('retry_count', 0)

    if retry_count >= MAX_RETRIES:
        logger.error("%s: max retries (%d) exceeded, giving up until next scheduled run",
                     action_id, MAX_RETRIES, extra=ref_extra)
        data.pop('retry_after', None)
        data.pop('retry_count', None)
        action.data = data
        action.save(update_fields=['data'])
        return False

    backoff_minutes = RETRY_BACKOFF_MINUTES[min(retry_count, len(RETRY_BACKOFF_MINUTES) - 1)]
    retry_at = time.time() + backoff_minutes * 60
    data['retry_after'] = retry_at
    data['retry_count'] = retry_count + 1
    action.data = data
    action.save(update_fields=['data'])
    logger.info("%s: scheduled retry %d/%d in %d minutes",
                action_id, retry_count + 1, MAX_RETRIES, backoff_minutes,
                extra=ref_extra)
    return True


def _clear_retry(action_id):
    """Clear retry state on successful completion."""
    action = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id=action_id,
    ).first()
    if not action:
        return
    data = action.data or {}
    changed = False
    for key in ('retry_after', 'retry_count'):
        if key in data:
            del data[key]
            changed = True
    if changed:
        action.data = data
        action.save(update_fields=['data'])


def main():
    if len(sys.argv) < 2:
        logger.error("Usage: agent_complete.py <action_entry_id> [exit_code]")
        return

    action_id = sys.argv[1]
    exit_code = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    stderr_file = sys.argv[3] if len(sys.argv) > 3 else None
    now = time.time()

    # Look up which entry this agent was working on for per-entry logging
    current_entry = SysConfig.objects.filter(
        key=f'agent_{action_id}_entry'
    ).values_list('value', flat=True).first()
    tracking_uuid = SysConfig.objects.filter(
        key=f'agent_{action_id}_tracking'
    ).values_list('value', flat=True).first()
    launched_ts = SysConfig.objects.filter(
        key=f'agent_{action_id}_launched'
    ).values_list('value', flat=True).first()
    duration_sec = round(now - float(launched_ts)) if launched_ts else None

    status = 'completed' if exit_code in (0, 124) else 'failed'
    ref_extra = {'action_id': action_id, 'run_status': status,
                 'exit_code': exit_code}
    if current_entry:
        ref_extra['entry_id'] = current_entry
    if tracking_uuid:
        ref_extra['tracking'] = tracking_uuid
    if duration_sec is not None:
        ref_extra['duration_sec'] = duration_sec
    logger.info("%s: exit_code=%d, status=%s", action_id, exit_code, status,
                extra=ref_extra)

    # Log captured stderr from the claude subprocess
    stderr_content = ''
    if stderr_file:
        try:
            stderr_content = open(stderr_file).read().strip()
            os.unlink(stderr_file)
            if stderr_content:
                log_fn = logger.error if exit_code not in (0, 124) else logger.info
                for line in stderr_content.split('\n'):
                    log_fn("%s stderr: %s", action_id, line,
                           extra=ref_extra)
        except Exception as e:
            logger.error("%s: failed to read stderr file %s: %s",
                         action_id, stderr_file, e, extra=ref_extra)

    # On timeout, wait for subagents before finalizing
    subagent_count = 0
    if exit_code == 124 and current_entry:
        subagent_count = _wait_for_subagents(action_id, current_entry, ref_extra)

    now = time.time()  # Refresh after possible wait

    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_status',
        defaults={'value': status, 'timestamp_modified': now})
    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_completed',
        defaults={'value': str(now), 'timestamp_modified': now})

    # Update last_activity from the tracking entry's final timestamp.
    tracking_uuid = SysConfig.objects.filter(
        key=f'agent_{action_id}_tracking'
    ).values_list('value', flat=True).first()
    if tracking_uuid:
        tracking_ts = Entry.objects.filter(
            id=tracking_uuid, deleted_at__isnull=True,
        ).values_list('timestamp_modified', flat=True).first()
        if tracking_ts:
            SysConfig.objects.update_or_create(
                key=f'agent_{action_id}_last_activity',
                defaults={'value': str(float(tracking_ts)),
                          'timestamp_modified': now})

    # Structured error reporting and retry scheduling
    if exit_code not in (0, 124):
        error_msg = f"Agent exited {exit_code}"
        if stderr_content:
            error_msg += f": {stderr_content[-200:]}"
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_last_error',
            defaults={'value': error_msg, 'timestamp_modified': now})
        SysConfig.objects.update_or_create(
            key=f'agent_{action_id}_last_error_time',
            defaults={'value': str(now), 'timestamp_modified': now})
        # Schedule retry with backoff
        _schedule_retry(action_id, ref_extra)
    else:
        SysConfig.objects.filter(key=f'agent_{action_id}_last_error').update(
            value='', timestamp_modified=now)
        SysConfig.objects.filter(key=f'agent_{action_id}_last_error_time').update(
            value='', timestamp_modified=now)
        # Clear retry state on success
        _clear_retry(action_id)

    # Write structured run result to the current entry's data field
    if current_entry:
        try:
            entry = Entry.objects.filter(
                id=current_entry, deleted_at__isnull=True
            ).first()
            if entry:
                data = entry.data if isinstance(entry.data, dict) else {}
                launched = SysConfig.objects.filter(
                    key=f'agent_{action_id}_launched'
                ).values_list('value', flat=True).first()
                duration = round(now - float(launched)) if launched else None
                data['run_status'] = status
                data['run_completed_at'] = now
                data['run_duration_seconds'] = duration
                data['run_exit_code'] = exit_code
                if subagent_count:
                    data['subagent_count'] = subagent_count
                if exit_code not in (0, 124) and stderr_content:
                    data['run_error'] = stderr_content[-200:]
                elif 'run_error' in data:
                    del data['run_error']
                entry.data = data
                entry.save(update_fields=['data'])
                logger.info("%s: wrote run result to entry %s (duration=%ss, subagents=%d)",
                            action_id, current_entry,
                            duration, subagent_count, extra=ref_extra)
        except Exception as e:
            logger.error("%s: failed to write run result: %s",
                         action_id, e, extra=ref_extra)

    # Post-process synthesis entries: convert plain source report references to md links
    if current_entry and exit_code in (0, 124):
        _linkify_synthesis_sources(current_entry)

    # Queue drain for research-agent: auto-chain to next pending item
    if action_id == 'research-agent' and exit_code in (0, 124):
        _research_queue_drain(now)
        # Check if multimodel synthesis should be triggered
        if current_entry:
            _check_and_trigger_synthesis(current_entry)


def _research_queue_drain(now):
    """Chain to the next pending research item, or stop if requested."""
    # Check stop request
    stop_req = SysConfig.objects.filter(key='research_stop_requested').first()
    if stop_req and stop_req.value:
        logger.info("research-agent: stop requested, not chaining")
        stop_req.value = ''
        stop_req.timestamp_modified = now
        stop_req.save(update_fields=['value', 'timestamp_modified'])
        return

    # Find next pending research item (sorted by priority then FIFO)
    research_ids = Tag.objects.filter(
        tag_name='research_topic'
    ).values_list('entry_id', flat=True)
    next_item = Entry.objects.filter(
        id__in=research_ids,
        kind='memory',
        deleted_at__isnull=True,
    ).exclude(status='done').order_by('priority', 'timestamp_created').first()

    if not next_item:
        logger.info("research-agent: queue empty, not chaining")
        return

    # Chain: set SPECIFIC TARGET so Claude doesn't have to guess
    research_action = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='research-agent',
    ).first()
    if not research_action:
        logger.error("research-agent: action entry not found, cannot chain")
        return

    data = research_action.data or {}
    data['last_run'] = 0
    data['next_target'] = (
        f"SPECIFIC TARGET:\nEntry UUID: {next_item.id}\n"
        f"Topic: {next_item.content}"
    )
    data['next_target_entry_id'] = str(next_item.id)
    research_action.data = data
    research_action.timestamp_modified = now
    research_action.save(update_fields=['data', 'timestamp_modified'])

    next_entry_id = (next_item.data or {}).get('entry_id', str(next_item.id)[:8])
    logger.info("research-agent: chaining to %s — %s",
                next_entry_id, next_item.content[:60])

    # Wake action agent via sysconfig flag
    SysConfig.objects.update_or_create(
        key='action_agent_wake_requested',
        defaults={'value': '1', 'timestamp_modified': now})

    # Also dispatch Gemini and ChatGPT in parallel
    topic_text = next_item.content.split('\n')[0].strip()
    base_entry_id = (next_item.data or {}).get('entry_id')
    if base_entry_id and topic_text:
        from tjai_app.action_runner import dispatch_multimodel
        dispatch_multimodel(
            topic_text=topic_text,
            base_entry_id=base_entry_id,
            base_uuid=str(next_item.id),
            context_obj=next_item.context,
        )


def _linkify_synthesis_sources(current_entry_uuid):
    """Convert plain-text source report entry_ids to markdown links in synthesis entries."""
    entry = Entry.objects.filter(
        id=current_entry_uuid, deleted_at__isnull=True,
    ).first()
    if not entry:
        return
    data = entry.data if isinstance(entry.data, dict) else {}
    if data.get('model') != 'synthesis':
        return

    content = entry.content
    changed = False
    for key in ('source_claude_entry_id', 'source_gemini_entry_id', 'source_chatgpt_entry_id'):
        eid = data.get(key)
        if not eid:
            continue
        md_link = f'[{eid}](/tjai/entry/?entry_id={eid})'
        # Replace bare entry_id references that aren't already inside a markdown link
        # Match the entry_id when NOT preceded by ( or [ (already linked)
        if eid in content and md_link not in content:
            # Only replace occurrences that are plain text, not already in a link
            import re
            # Negative lookbehind for ( or [ to avoid re-linking
            pattern = re.compile(r'(?<!\()(?<!\[)' + re.escape(eid) + r'(?!\])')
            new_content = pattern.sub(md_link, content)
            if new_content != content:
                content = new_content
                changed = True

    if changed:
        entry.content = content
        entry.save(update_fields=['content'])
        logger.info("Linkified source reports in synthesis entry %s",
                     current_entry_uuid)


def _check_and_trigger_synthesis(current_entry_uuid):
    """Check if all 3 models are done and trigger synthesis.

    Called after Claude finishes a research item. Delegates to the shared
    implementation in research_multimodel.py to avoid duplicating logic.
    """
    entry = Entry.objects.filter(
        id=current_entry_uuid, deleted_at__isnull=True,
    ).first()
    if not entry:
        return

    data = entry.data if isinstance(entry.data, dict) else {}
    entry_id = data.get('entry_id')
    if not entry_id:
        return

    # Only applies to base research entries (not -gemini, -chatgpt, -synthesis)
    if any(entry_id.endswith(suffix) for suffix in ('-gemini', '-chatgpt', '-synthesis')):
        return

    # Check if gemini and chatgpt entries exist at all — if they don't,
    # this topic wasn't submitted with multimodel dispatch
    gemini = Entry.objects.filter(
        data__entry_id=f'{entry_id}-gemini', deleted_at__isnull=True,
    ).first()
    chatgpt = Entry.objects.filter(
        data__entry_id=f'{entry_id}-chatgpt', deleted_at__isnull=True,
    ).first()

    if not gemini or not chatgpt:
        return  # No multimodel dispatch for this topic

    if not all(e.status == 'done' for e in [entry, gemini, chatgpt]):
        logger.info("Synthesis check: not all models done (base=%s, gemini=%s, chatgpt=%s)",
                     entry.status, gemini.status, chatgpt.status)
        return

    # Check if synthesis already exists
    synth_entry_id = f'{entry_id}-synthesis'
    existing = Entry.objects.filter(
        data__entry_id=synth_entry_id, deleted_at__isnull=True,
    ).first()
    if existing:
        logger.info("Synthesis entry %s already exists", synth_entry_id)
        return

    # Delegate to research_multimodel's synthesis creation
    logger.info("All 3 models done for %s — triggering synthesis", entry_id)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'research_multimodel',
            str(Path(__file__).parent / 'research_multimodel.py'),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._create_and_dispatch_synthesis(entry_id, entry, synth_entry_id)
    except Exception as e:
        logger.error("Failed to trigger synthesis for %s: %s", entry_id, e)


try:
    main()
except Exception:
    # Last resort: log to DB even if everything else fails
    try:
        logger.error("agent_complete.py crashed: %s",
                     __import__('traceback').format_exc())
    except Exception:
        pass  # DB itself is down — nothing we can do
