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

    # Fetch current entry once for run result + post-processing
    entry = None
    if current_entry:
        entry = Entry.objects.filter(
            id=current_entry, deleted_at__isnull=True
        ).first()

    # Write structured run result to the current entry's data field
    if entry:
        try:
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
            # For research-agent: system takes responsibility for marking done.
            # Don't rely on the AI to do it — it may spawn subagents and never
            # reach the final step.  Successful or timed-out runs are "done".
            update_fields = ['data']
            if action_id == 'research-agent':
                if exit_code in (0, 124):
                    entry.status = 'done'
                else:
                    entry.status = 'blocked'
                update_fields.append('status')
            entry.data = data
            entry.save(update_fields=update_fields)
            logger.info("%s: wrote run result to entry %s (duration=%ss, subagents=%d)",
                        action_id, current_entry,
                        duration, subagent_count, extra=ref_extra)
        except Exception as e:
            logger.error("%s: failed to write run result: %s",
                         action_id, e, extra=ref_extra)

    # Post-process synthesis entries: convert plain source report references to md links
    if current_entry and exit_code in (0, 124):
        _linkify_synthesis_sources(current_entry)

    # Post-process daily-history: extract digested history to file for KozyKorner
    if action_id == 'daily-history' and exit_code in (0, 124):
        try:
            # entry may be None (daily-history doesn't set next_target_entry_id).
            # Derive date from today and look up the daily entry directly.
            from tjai_app.services import get_timezone
            from datetime import datetime
            date_str = datetime.now(get_timezone()).date().isoformat()
            from extract_history import process_date
            if process_date(date_str):
                logger.info("daily-history: extracted history HTML for %s", date_str)
            else:
                logger.warning("daily-history: no history section found for %s", date_str)
        except Exception as e:
            logger.error("daily-history: extract_history failed: %s", e)

    # Post-process ideation: set entry_id on log, append link to synopsis
    if action_id == 'ideation-agent' and exit_code in (0, 124):
        try:
            from datetime import datetime
            from tjai_app.services import get_timezone
            from synopsis_utils import find_daily_entry, append_section

            today = datetime.now(get_timezone()).date()
            date_str = today.isoformat()
            yyyymmdd = today.strftime('%Y%m%d')
            entry_id = f'ideation_{yyyymmdd}'

            # Find the ideation log entry (created in last hour) and stamp entry_id
            log_tag = Tag.objects.filter(tag_name='ideation-log').values_list('entry_id', flat=True)
            log_entry = Entry.objects.filter(
                id__in=log_tag,
                deleted_at__isnull=True,
                timestamp_created__gte=time.time() - 3600,
            ).order_by('-timestamp_created').first()
            if log_entry:
                data = log_entry.data if isinstance(log_entry.data, dict) else {}
                data['entry_id'] = entry_id
                log_entry.data = data
                log_entry.save(update_fields=['data'])
                logger.info("ideation-agent: set entry_id=%s on log %s", entry_id, log_entry.id)

            # Append link to daily synopsis
            synopsis = find_daily_entry(today)
            if synopsis:
                link = f'[Ideation \u2014 {date_str}](/tjai/entry/?entry_id={entry_id})'
                append_section(synopsis, 'Ideation', link)
                logger.info("ideation-agent: appended ideation link to daily-%s", date_str)
            else:
                logger.warning("ideation-agent: daily-%s not found, skipping synopsis link", date_str)
        except Exception as e:
            logger.error("ideation-agent: failed to post-process ideation: %s", e)

    # Post-process assessment: normalize field names to canonical schema
    if action_id == 'llm-assessment' and exit_code in (0, 124):
        try:
            from normalize_assessment import normalize_date
            from tjai_app.services import get_timezone
            from datetime import datetime
            date_str = datetime.now(get_timezone()).date().isoformat()
            normalize_date(date_str)
        except Exception as e:
            logger.error("llm-assessment: normalize failed: %s", e)

    # Research-agent post-processing: model completion first, then queue drain.
    # Order matters — synthesis must set next_target before queue drain overwrites it.
    if action_id == 'research-agent' and exit_code in (0, 124):
        # Update base entry tracking and check if all 3 models are done
        if entry:
            entry_data = entry.data if isinstance(entry.data, dict) else {}
            if (entry_data.get('source') == 'multimodel'
                    and entry_data.get('model')
                    and entry_data.get('model') != 'synthesis'):
                try:
                    from research_multimodel import research_model_complete
                    research_model_complete(entry)
                except Exception as e:
                    logger.error("research_model_complete failed: %s", e,
                                 extra=ref_extra)
        _research_queue_drain(now)

    # Claude failure on multimodel entry: mark model status as blocked on base
    if action_id == 'research-agent' and exit_code not in (0, 124) and entry:
        entry_data = entry.data if isinstance(entry.data, dict) else {}
        if entry_data.get('source') == 'multimodel' and entry_data.get('model'):
            model = entry_data['model']
            base_entry_id = entry_data.get('base_entry_id')
            if base_entry_id:
                try:
                    base = Entry.objects.filter(
                        data__entry_id=base_entry_id, deleted_at__isnull=True,
                    ).first()
                    if base:
                        bd = base.data if isinstance(base.data, dict) else {}
                        bd[f'{model}_status'] = 'blocked'
                        base.data = bd
                        base.save(update_fields=['data'])
                        logger.info("%s: set %s_status=blocked on base %s",
                                    action_id, model, base_entry_id,
                                    extra=ref_extra)
                except Exception as e:
                    logger.error("%s: failed to update base on failure: %s",
                                 action_id, e, extra=ref_extra)


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

    # Find next pending PRIMARY research item (sorted by priority then FIFO)
    # Derivatives (gemini, chatgpt, synthesis) all have data.source='multimodel'
    research_ids = Tag.objects.filter(
        tag_name='research_topic'
    ).values_list('entry_id', flat=True)
    next_item = Entry.objects.filter(
        id__in=research_ids,
        kind='memory',
        deleted_at__isnull=True,
    ).exclude(
        status='done'
    ).exclude(
        status='active'            # models already dispatched
    ).exclude(
        data__source='multimodel'
    ).exclude(
        data__has_key='run_status'     # skip already-researched entries
    ).order_by('priority', 'timestamp_created').first()

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

    # Wake action agent via sysconfig flag —
    # execute_action will handle creating all 3 model entries and dispatching
    SysConfig.objects.update_or_create(
        key='action_agent_wake_requested',
        defaults={'value': '1', 'timestamp_modified': now})


def _linkify_synthesis_sources(current_entry_uuid):
    """Fix source report references in synthesis entries.

    Handles common AI mistakes:
    - Backtick-wrapped markdown links: `[text](url)` → [text](url)
    - Suffixes outside URL: [eid](url)-gemini → [eid-gemini](url-gemini)
    - Bare entry_ids not linked at all
    """
    import re

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

    # Fix 1: Strip backticks around markdown links — `[text](url)` → [text](url)
    backtick_link = re.compile(r'`(\[[^\]]+\]\([^\)]+\))`')
    new_content = backtick_link.sub(r'\1', content)
    if new_content != content:
        content = new_content
        changed = True

    # Fix 2: For each source entry_id, ensure proper markdown links exist
    for key in ('source_claude_entry_id', 'source_gemini_entry_id', 'source_chatgpt_entry_id'):
        eid = data.get(key)
        if not eid:
            continue
        md_link = f'[{eid}](/tjai/entry/?entry_id={eid})'

        # Fix suffix outside URL paren: [base-eid](url)-suffix → [full-eid](full-url)
        # e.g. [research-foo](/tjai/entry/?entry_id=research-foo)-gemini
        base_eid = data.get('base_entry_id', '')
        if base_eid and eid != base_eid and eid.startswith(base_eid):
            suffix = eid[len(base_eid):]  # e.g. "-gemini"
            bad_pattern = re.compile(
                re.escape(f'[{base_eid}](/tjai/entry/?entry_id={base_eid})') +
                re.escape(suffix)
            )
            new_content = bad_pattern.sub(md_link, content)
            if new_content != content:
                content = new_content
                changed = True

        # Fix bare entry_ids not already in a markdown link
        if eid in content and md_link not in content:
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



try:
    main()
except Exception:
    # Last resort: log to DB even if everything else fails
    try:
        logger.error("agent_complete.py crashed: %s",
                     __import__('traceback').format_exc())
    except Exception:
        pass  # DB itself is down — nothing we can do
