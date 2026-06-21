import json
import logging
import os
import re
import time
import uuid
from html import escape as html_escape, unescape as html_unescape
from datetime import datetime, timedelta
from collections import Counter
from urllib.parse import quote

from .dialog_context import CURRENT_DIALOG_CONTEXT, DIALOG_CONTEXTS, DIALOG_TAG

logger = logging.getLogger(__name__)

_LIST_RE = re.compile(r'[-*+] |\d+\. ')
_LIST_MARKER_RE = re.compile(r'^(\s*)([-*+] |\d+\. )')
_BLOCK_RE = re.compile(r'^(\s*)([-*+] |\d+\. |#{1,6} |```)')

def _deindent_paragraph_list_blocks(lines):
    """Normalize indented list blocks that start after paragraph text."""
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        marker = _LIST_MARKER_RE.match(line)
        if (marker and marker.group(1) and i > 0
                and lines[i - 1].strip()
                and not _LIST_RE.match(lines[i - 1].lstrip())):
            base_indent = len(marker.group(1))
            if result and result[-1].strip():
                result.append('')
            while i < len(lines):
                block_line = lines[i]
                if not block_line.strip():
                    result.append(block_line)
                    i += 1
                    continue
                cur_indent = len(block_line) - len(block_line.lstrip())
                if cur_indent < base_indent:
                    break
                result.append(block_line[base_indent:])
                i += 1
            continue
        result.append(line)
        i += 1
    return result


def _fix_md_list_spacing(text):
    """Fix two common markdown list issues:

    1. Insert blank line before list items that follow a non-list, non-blank
       line (AI-generated markdown often omits this).
    2. Rejoin broken continuation lines — when a list item's text was
       hard-wrapped and the continuation starts at column 0 (or below the
       list content indent), the markdown parser loses nesting context.
    """
    lines = _deindent_paragraph_list_blocks(text.split('\n'))
    result = []
    for i, line in enumerate(lines):
        # Rejoin broken list continuations: non-blank, non-block line whose
        # indent is less than the previous list item's content column.
        if (i > 0 and line.strip() and result
                and not _BLOCK_RE.match(line)):
            prev = result[-1]
            pm = re.match(r'^(\s*)([-*+] |\d+\. )', prev)
            if pm:
                content_col = len(pm.group(1)) + len(pm.group(2))
                cur_indent = len(line) - len(line.lstrip())
                if cur_indent < content_col:
                    result[-1] = prev + ' ' + line.strip()
                    continue

        if (i > 0
                and _LIST_RE.match(line.lstrip())
                and lines[i - 1].strip()
                and not _LIST_RE.match(lines[i - 1].lstrip())):
            result.append('')
        result.append(line)
    return '\n'.join(result)


# HTML elements that put the parser into rcdata / raw-text / plaintext mode,
# swallowing all following page content until their (often absent) close tag.
# A literal '<title>' in entry content — e.g. from a commit message like
# "PR #<N>: <title>" — truncated the 2026-04-23 daily synopsis mid-render.
# None of these tags have any legitimate place in rendered tjai entry content,
# so escaping them is side-effect-free across every markdown render path.
_RAW_HTML_HAZARD_TAGS = {
    'html', 'head', 'body', 'base', 'link', 'meta', 'title', 'script',
    'style', 'textarea', 'iframe', 'noscript', 'noembed', 'noframes',
    'xmp', 'plaintext',
}
_RAW_HTML_HAZARD_PREFIXES = {
    tag[:n]
    for tag in _RAW_HTML_HAZARD_TAGS
    for n in range(3, len(tag) + 1)
}
_RAW_HTML_TAG_RE = re.compile(r'</?([A-Za-z][A-Za-z0-9:-]*)(?:\s[^>\n]*)?>?', re.IGNORECASE)


def _neutralize_raw_html_hazards(text):
    def repl(match):
        tag = match.group(1).lower()
        if tag not in _RAW_HTML_HAZARD_PREFIXES:
            return match.group(0)
        return match.group(0).replace('<', '&lt;').replace('>', '&gt;')
    return _RAW_HTML_TAG_RE.sub(repl, text)


def _render_markdown(text, extensions=None):
    """Unified entry-content render: list-spacing fix + markdown + hazard-tag
    neutralization. Use this instead of markdown.markdown() directly so that
    every render path gets the same safety post-processing."""
    import markdown
    if not text:
        return ''
    exts = extensions if extensions is not None else ['nl2br', 'tables', 'fenced_code']
    safe_text = _neutralize_raw_html_hazards(text)
    html = markdown.markdown(_fix_md_list_spacing(safe_text), extensions=exts, tab_length=2)
    html = _neutralize_raw_html_hazards(html)
    return _render_text_fences(html)


_BARE_URL_RE = re.compile(r'https?://[^\s<]+')
_HTML_TAG_RE = re.compile(r'(<[^>]+>)')
_LINKIFY_SKIP_TAGS = {'a', 'code', 'pre', 'script', 'style'}


def _linkify_rendered_html(html):
    """Linkify bare URLs in rendered HTML text nodes.

    Regexing the whole HTML string misses common cases such as
    ``<li>https://example.com/</li>`` if the regex excludes URLs preceded by
    ``>`` to avoid href attributes. Splitting tags keeps attributes untouched
    and lets us skip anchors/code blocks explicitly.
    """
    if not html:
        return ''
    parts = _HTML_TAG_RE.split(html)
    stack = []
    out = []

    def linkify_text(text):
        def repl(match):
            url = match.group(0)
            suffix = ''
            while url and url[-1] in '.,;:!?':
                suffix = url[-1] + suffix
                url = url[:-1]
            href = html_escape(html_unescape(url), quote=True)
            return f'<a target="_blank" href="{href}">{url}</a>{suffix}'
        return _BARE_URL_RE.sub(repl, text)

    for part in parts:
        if not part:
            continue
        if part.startswith('<'):
            close = re.match(r'</\s*([A-Za-z0-9:-]+)', part)
            if close:
                tag = close.group(1).lower()
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i] == tag:
                        del stack[i:]
                        break
                out.append(part)
                continue
            open_tag = re.match(r'<\s*([A-Za-z0-9:-]+)\b', part)
            if open_tag and not part.rstrip().endswith('/>'):
                stack.append(open_tag.group(1).lower())
            out.append(part)
        elif any(tag in _LINKIFY_SKIP_TAGS for tag in stack):
            out.append(part)
        else:
            out.append(linkify_text(part))
    return ''.join(out)


# A ```text``` fence is prose (emails, PR/commit bodies), not code: keep the
# monospace box but make links live. markdown emits <pre><code class="language-text">
# and never parses link syntax inside a fence; Prism would also re-render the block
# from its textContent, discarding any anchors injected server-side. So drop the
# language class (Prism then ignores the block), turn [text](url) and bare URLs into
# anchors, and restyle through the .text-fence class. Real code fences (```python,
# ```bash, …) are left untouched. Keep in sync with scripts/md_render.py.
_TEXT_FENCE_RE = re.compile(r'<pre><code class="language-text">(.*?)</code></pre>', re.DOTALL)
_MD_LINK_RE = re.compile(r'\[([^\]\n]+)\]\((https?://[^)\s]+)\)')


def _linkify_text_fence_content(content):
    def md_link(m):
        href = html_escape(html_unescape(m.group(2)), quote=True)
        return f'<a target="_blank" href="{href}">{m.group(1)}</a>'
    # Convert explicit [disp](url) links first, then linkify remaining bare URLs
    # (the tag-aware linkifier leaves the anchors just created alone).
    return _linkify_rendered_html(_MD_LINK_RE.sub(md_link, content))


def _render_text_fences(html):
    def repl(m):
        return ('<pre class="text-fence"><code>'
                + _linkify_text_fence_content(m.group(1))
                + '</code></pre>')
    return _TEXT_FENCE_RE.sub(repl, html)


_XML_LIKE_RE = re.compile(
    r'^\s*<(?:(?:rule|instruction|why|when|task-notification|system|profile)\b|[A-Za-z][A-Za-z0-9:_-]*[\s>/])',
    re.IGNORECASE,
)


def _looks_xml_like(text):
    return bool(text and _XML_LIKE_RE.match(text))


def _render_xml_code(text):
    if not text:
        return ''
    from django.utils.html import escape
    escaped = escape(text)
    return f'<pre class="language-markup"><code class="language-markup">{escaped}</code></pre>'

from .tjai_utils import fmt_datetime, fmt_date, fmt_time, fmt_duration, fmt_ago, get_app_tz
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.urls import reverse

from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.http import Http404
from django.conf import settings as django_settings
from .models import AppLog, Context, Entry, KozyChat, Relation, RssItem, Tag, TagStats, SubNote, Machine, SysConfig


AGENT_GRACE_SECONDS = 300  # 5 min — never touch an agent younger than this


def _log_research(level, message, entry_id=None):
    """Write to AppLog with optional per-entry reference. Visible on agent-log page."""
    from django.utils import timezone as tz
    AppLog.objects.create(
        source='research',
        timestamp=tz.now(),
        level=level,
        levelname=logging.getLevelName(level),
        message=message,
        extra_data={'entry_id': entry_id} if entry_id else None,
    )


def _wake_action_agent():
    """Wake action agent daemon via sysconfig flag. Returns (ok, message).

    We write a sysconfig key that the agent polls every few seconds.
    Cannot use os.kill(SIGHUP) because Apache runs as www-data and
    the agent runs as admin — different users, no signal permission.
    """
    now = time.time()
    SysConfig.objects.update_or_create(
        key='action_agent_wake_requested',
        defaults={'value': '1', 'timestamp_modified': now},
    )
    return True, None


def _heal_stale_agent(status_key, launched_key, agent_name):
    """Check if a running agent is truly dead and clean up if so.

    Rules:
    1. NEVER intervene within AGENT_GRACE_SECONDS of launch.
    2. After grace: only clean up if process is confirmed dead (process_alive='0').
    3. If process is alive, do NOT kill or reset — just report.
    4. Set status to 'failed' (not 'idle') so errors are visible.
    """
    status_val = SysConfig.objects.filter(
        key=status_key
    ).values_list('value', flat=True).first() or 'idle'
    launched_val = SysConfig.objects.filter(
        key=launched_key
    ).values_list('value', flat=True).first()

    if status_val != 'running':
        return status_val, launched_val

    action_id = status_key.split('agent_', 1)[1].rsplit('_status', 1)[0]
    now = time.time()
    launch_age = (now - float(launched_val)) if launched_val else 0

    # Rule 1: never touch agents in their grace period
    if launch_age < AGENT_GRACE_SECONDS:
        return status_val, launched_val

    # Read watchdog diagnostics
    process_alive = SysConfig.objects.filter(
        key=f'agent_{action_id}_process_alive'
    ).values_list('value', flat=True).first()

    # Rule 2: only clean up if process is confirmed dead
    if process_alive != '0':
        return status_val, launched_val

    # Process is dead and agent_complete didn't set status (still 'running').
    current_entry = SysConfig.objects.filter(
        key=f'agent_{action_id}_entry'
    ).values_list('value', flat=True).first()

    # Rule 3: don't mark failed if the current entry is waiting on a remote
    # worker (e.g. gemma on the Mac Studio). Remote workers have no local
    # process — process_alive=='0' is normal, not a failure condition.
    # Also check if any model sub-entries of the base topic have
    # worker_target set (gemma running while claude is done).
    if current_entry:
        cur = Entry.objects.filter(
            id=current_entry, deleted_at__isnull=True,
        ).first()
        if cur:
            cur_data = cur.data if isinstance(cur.data, dict) else {}
            # Direct: the current entry is itself a remote-worker entry
            if cur_data.get('worker_target'):
                return status_val, launched_val
            # Indirect: base entry with active or staged remote-worker sub-entries
            base_eid = cur_data.get('entry_id')
            if base_eid:
                from .action_runner import RESEARCH_MODELS
                for m in RESEARCH_MODELS:
                    sub = Entry.objects.filter(
                        data__entry_id=f'{base_eid}-{m}',
                        deleted_at__isnull=True,
                    ).first()
                    if sub:
                        sub_data = sub.data if isinstance(sub.data, dict) else {}
                        if sub_data.get('worker_target') and sub.status in (
                                'active', None):
                            return status_val, launched_val

    elapsed_str = f' after {int(launch_age)}s' if launched_val else ''
    error_msg = f"Agent process died{elapsed_str} without completing"

    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_last_error',
        defaults={'value': error_msg, 'timestamp_modified': now})
    SysConfig.objects.update_or_create(
        key=f'agent_{action_id}_last_error_time',
        defaults={'value': str(now), 'timestamp_modified': now})

    _log_research(logging.ERROR, f"{agent_name}: {error_msg}",
                  entry_id=current_entry)

    logger.warning("%s process dead, marking failed", agent_name)
    SysConfig.objects.update_or_create(
        key=status_key,
        defaults={'value': 'failed', 'timestamp_modified': now})
    status_val = 'failed'

    return status_val, launched_val


def api_health(request):
    """Health check endpoint. Also exposes app timezone for external clients."""
    return JsonResponse({"status": "ok", "timezone": str(get_app_tz())})


@csrf_exempt
@require_http_methods(["POST"])
def sync_push(request):
    """
    Receive dirty entries from a client and upsert into server database.

    Request body:
    {
        "machine_id": "uuid",
        "entries": [...],
        "contexts": [...],
        "tags": [...],
        "sub_notes": [...]
    }

    Response:
    {
        "status": "ok",
        "received": {"entries": N, "contexts": N, "tags": N, "sub_notes": N}
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    machine_id = data.get("machine_id")
    if not machine_id:
        return JsonResponse({"error": "machine_id required"}, status=400)

    # Update machine last_sync
    now = time.time()
    Machine.objects.update_or_create(
        machine_id=machine_id,
        defaults={
            "hostname": data.get("hostname"),
            "ip_address": request.META.get("REMOTE_ADDR"),
            "last_sync": now,
            "timestamp_created": now,
            "is_active": 1,
        }
    )

    counts = {"entries": 0, "contexts": 0, "tags": 0, "sub_notes": 0}

    # Upsert contexts
    for ctx in data.get("contexts", []):
        Context.objects.update_or_create(
            name=ctx["name"],
            defaults={
                "title": ctx.get("title"),
                "description": ctx.get("description"),
                "timestamp_created": ctx["timestamp_created"],
                "timestamp_modified": ctx.get("timestamp_modified", now),
            }
        )
        counts["contexts"] += 1

    # Upsert entries
    for entry in data.get("entries", []):
        # Parse data field if client sent JSON string (SQLite stores as text)
        entry_data = entry.get("data")
        if isinstance(entry_data, str):
            try:
                entry_data = json.loads(entry_data)
            except json.JSONDecodeError as e:
                logger.warning("Malformed JSON in entry %s data during sync: %s",
                               entry.get("id", "?"), e)
                entry_data = None
        Entry.objects.update_or_create(
            id=entry["id"],
            defaults={
                "parent_id": entry.get("parent_id"),
                "content": entry["content"],
                "kind": entry["kind"],
                "timestamp_created": entry["timestamp_created"],
                "timestamp_modified": entry.get("timestamp_modified", now),
                "context_id": entry.get("context"),
                "is_dirty": 0,  # Server copy is clean
                "deleted_at": entry.get("deleted_at"),
                "name": entry.get("name"),
                "priority": entry.get("priority"),
                "status": entry.get("status"),
                "data": entry_data,
                "mmdd": entry.get("mmdd"),
            }
        )
        counts["entries"] += 1

    # Replace tags for pushed (dirty) entries: delete existing, then insert new
    # This ensures tag removals are synced properly
    pushed_entry_ids = [e["id"] for e in data.get("entries", [])]
    if pushed_entry_ids:
        Tag.objects.filter(entry_id__in=pushed_entry_ids).delete()

    for tag in data.get("tags", []):
        Tag.objects.create(
            tag_name=tag["tag_name"],
            entry_id=tag["entry_id"],
        )
        counts["tags"] += 1

    # Auto-tag bookmarks arriving via sync
    from .tagger import tag_bookmark
    for entry in data.get("entries", []):
        if entry["kind"] == "bookmark" and not entry.get("deleted_at"):
            try:
                tag_bookmark(Entry.objects.get(id=entry["id"]))
            except Entry.DoesNotExist:
                logger.warning("tag_bookmark: entry %s not found after sync upsert",
                               entry["id"])

    # Upsert sub_notes
    for note in data.get("sub_notes", []):
        note_data = note.get("data")
        if isinstance(note_data, str):
            try:
                note_data = json.loads(note_data)
            except json.JSONDecodeError as e:
                logger.warning("Malformed JSON in sub_note %s data during sync: %s",
                               note.get("id", "?"), e)
                note_data = None
        SubNote.objects.update_or_create(
            id=note["id"],
            defaults={
                "parent_id": note["parent_id"],
                "content": note["content"],
                "timestamp_created": note["timestamp_created"],
                "data": note_data,
            }
        )
        counts["sub_notes"] += 1

    if counts["tags"] > 0:
        from .tag_stats import rebuild_tag_stats
        rebuild_tag_stats()

    return JsonResponse({"status": "ok", "received": counts})


SYNC_BATCH_SIZE = 500


@csrf_exempt
@require_http_methods(["GET"])
def sync_pull(request):
    """
    Return entries modified since a given timestamp, paginated.

    Query params:
        since: Unix timestamp (float). Returns entries with timestamp_modified > since.
        after_id: Entry ID for cursor-based pagination (handles same-timestamp boundaries).
        machine_id: Client machine ID (for tracking).

    Response:
    {
        "status": "ok",
        "server_time": <current server timestamp>,
        "entries": [...],
        "contexts": [...],
        "tags": [...],
        "sub_notes": [...],
        "has_more": true/false
    }
    """
    since = float(request.GET.get("since", 0))
    after_id = request.GET.get("after_id", "")
    machine_id = request.GET.get("machine_id")

    now = time.time()

    # Update machine tracking
    if machine_id:
        Machine.objects.update_or_create(
            machine_id=machine_id,
            defaults={
                "last_sync": now,
                "timestamp_created": now,
                "is_active": 1,
            }
        )

    # Get modified contexts (small table, no pagination needed)
    contexts = list(
        Context.objects.filter(timestamp_modified__gt=since).values(
            "name", "title", "description", "timestamp_created", "timestamp_modified"
        )
    )

    # Get modified entries — cursor-based pagination by (timestamp_modified, id)
    from django.db.models import Q
    if after_id:
        q = Q(timestamp_modified__gt=since) | Q(timestamp_modified=since, id__gt=after_id)
    else:
        q = Q(timestamp_modified__gt=since)

    entries = list(
        Entry.objects.filter(q).order_by("timestamp_modified", "id")
        .values(
            "id", "parent_id", "content", "kind", "timestamp_created",
            "timestamp_modified", "context_id", "deleted_at", "name",
            "priority", "status", "data", "mmdd"
        )[:SYNC_BATCH_SIZE]
    )
    has_more = len(entries) == SYNC_BATCH_SIZE

    # Rename context_id to context for client compatibility
    for e in entries:
        e["context"] = e.pop("context_id")

    # Get tags for this batch of entries
    entry_ids = [e["id"] for e in entries]
    tags = list(
        Tag.objects.filter(entry_id__in=entry_ids).values("tag_name", "entry_id")
    )

    # Get modified sub_notes (small table, no pagination needed)
    sub_notes = list(
        SubNote.objects.filter(timestamp_created__gt=since).values(
            "id", "parent_id", "content", "timestamp_created", "data"
        )
    )

    # Get all sysconfig (always returned, small table)
    sysconfig = {
        cfg["key"]: cfg["value"]
        for cfg in SysConfig.objects.values("key", "value")
    }

    return JsonResponse({
        "status": "ok",
        "server_time": now,
        "entries": entries,
        "contexts": contexts,
        "tags": tags,
        "sub_notes": sub_notes,
        "sysconfig": sysconfig,
        "has_more": has_more,
    })


# Remote inference worker — long-polling work dispatch
#
# A remote worker (e.g. tj_agent on a Mac Studio running ollama) long-polls
# /api/worker/poll with its machine_id and capabilities. The server holds the
# request up to WORKER_POLL_HOLD_SECONDS waiting for an unclaimed entry whose
# data.worker_target is in the requested capabilities. When found, the server
# atomically claims the entry (records worker_claimed_by, worker_claimed_at)
# and returns the prompt. The worker runs inference locally and POSTs the
# result to /api/worker/result. Stale claims (worker died mid-run) are
# automatically reclaimed after WORKER_CLAIM_STALE_SECONDS.

WORKER_POLL_HOLD_SECONDS = 50   # must stay under gunicorn --timeout (120)
WORKER_POLL_INTERVAL = 2        # DB check frequency during hold
# The stale-claim threshold governs both server-side auto-reclaim AND the
# display "zombie" rendering on the research page. It must comfortably
# exceed the longest legitimate single work-item runtime, not the longest
# single ollama call. With the Mac-side agent loop (multi-turn tool-use
# runs with lxr/github MCPs), a single codoc gemma4 work item can
# legitimately run ~1h. Margin on top of that → 2h.
# The earlier 30-min value dated from the assumption "1 work item = 1
# ollama call ≤ 30 min" and was junkifying the dashboard (healthy
# long-running agent runs showing as zombie/red).
WORKER_CLAIM_STALE_SECONDS = 2 * 60 * 60  # 2h — see note above
# Capability names a worker is allowed to advertise. Anything else is
# rejected at the worker_poll endpoint to prevent sysconfig pollution from
# typos or ad-hoc curl tests.
WORKER_CAPABILITIES = {'gemma4', 'gemma4-fast', 'qwen'}


def _claim_worker_entry(machine_id, capabilities):
    """Atomically claim one unclaimed entry matching the capabilities.

    Returns the claimed Entry or None. Also reclaims stale entries whose
    previous worker didn't report a result within WORKER_CLAIM_STALE_SECONDS.
    """
    from django.db import transaction
    now = time.time()
    stale_cutoff = now - WORKER_CLAIM_STALE_SECONDS

    for capability in capabilities:
        # Candidates: entries targeting this capability with active status.
        # JSONField equality filter ensures we only match entries with the key.
        candidates = Entry.objects.filter(
            data__worker_target=capability,
            status='active',
            deleted_at__isnull=True,
        ).order_by('timestamp_modified')

        for entry in candidates:
            data = entry.data if isinstance(entry.data, dict) else {}
            claimed_by = data.get('worker_claimed_by')
            claimed_at = data.get('worker_claimed_at')

            # Skip if currently claimed and not stale
            if claimed_by and claimed_at:
                try:
                    if float(claimed_at) > stale_cutoff:
                        continue  # still fresh, leave alone
                except (TypeError, ValueError):
                    pass  # malformed — treat as stale

            # Claim atomically
            try:
                with transaction.atomic():
                    locked = Entry.objects.select_for_update().filter(
                        id=entry.id, deleted_at__isnull=True,
                    ).first()
                    if not locked:
                        continue
                    ldata = locked.data if isinstance(locked.data, dict) else {}
                    # Re-check claim under lock
                    lclaimed = ldata.get('worker_claimed_by')
                    lclaimed_at = ldata.get('worker_claimed_at')
                    if lclaimed and lclaimed_at:
                        try:
                            if float(lclaimed_at) > stale_cutoff:
                                continue
                        except (TypeError, ValueError):
                            pass
                    ldata['worker_claimed_by'] = machine_id
                    ldata['worker_claimed_at'] = now
                    locked.data = ldata
                    locked.timestamp_modified = now
                    locked.save(update_fields=['data', 'timestamp_modified'])
                    # Upgrade base entry's {model}_status from 'staged' to 'active'
                    # so the UI knows a worker is actually processing.
                    base_eid = ldata.get('base_entry_id')
                    model = ldata.get('model')
                    if base_eid and model:
                        base = Entry.objects.filter(
                            data__entry_id=base_eid, deleted_at__isnull=True,
                        ).first()
                        if base:
                            bd = base.data if isinstance(base.data, dict) else {}
                            if bd.get(f'{model}_status') == 'staged':
                                bd[f'{model}_status'] = 'active'
                                base.data = bd
                                base.timestamp_modified = now
                                base.save(update_fields=['data', 'timestamp_modified'])
                    return locked
            except Exception as e:
                logger.warning("worker claim failed for %s: %s", entry.id, e)
                continue
    return None


@csrf_exempt
@require_http_methods(["GET"])
def worker_poll(request):
    """Long-polling work dispatch for remote inference workers.

    Query params:
        machine_id: stable worker ID (required)
        capabilities: comma-separated list (e.g. "gemma4")

    Response (work available):
        {"status": "ok", "work": {
            "entry_id": "<uuid>", "work_type": "research|codoc|generic",
            "model": "<capability>", "prompt": "<text>",
            "timeout_sec": <int>, "base_entry_id": "<id or null>"
        }}
        work_type is derived from the entry's data.source:
            'multimodel' → 'research', 'corun-ai' → 'codoc', else 'generic'.

    Response (no work within hold window):
        {"status": "ok", "work": null}
    """
    from django.utils import timezone as tz
    machine_id = request.GET.get("machine_id")
    if not machine_id:
        return JsonResponse({"error": "machine_id required"}, status=400)

    capabilities_str = request.GET.get("capabilities", "")
    capabilities = [c.strip() for c in capabilities_str.split(",") if c.strip()]
    if not capabilities:
        return JsonResponse({"error": "capabilities required"}, status=400)
    # Whitelist: reject unknown capability names so a typo or stray curl
    # cannot pollute worker_capability_*_lastpoll sysconfig with garbage
    # that future page renders would faithfully display as fake workers.
    unknown = [c for c in capabilities if c not in WORKER_CAPABILITIES]
    if unknown:
        return JsonResponse(
            {"error": f"unknown capabilities: {unknown}. "
                      f"known: {sorted(WORKER_CAPABILITIES)}"},
            status=400)

    # Update machine tracking on every poll (serves as heartbeat)
    now = time.time()
    Machine.objects.update_or_create(
        machine_id=machine_id,
        defaults={
            "last_sync": now,
            "timestamp_created": now,
            "is_active": 1,
        },
    )

    # Per-capability heartbeat — answers "is anyone listening for X?"
    # Key: worker_capability_{cap}_lastpoll → JSON {machine_id, ts}
    for cap in capabilities:
        SysConfig.objects.update_or_create(
            key=f'worker_capability_{cap}_lastpoll',
            defaults={'value': json.dumps({'machine_id': machine_id, 'ts': now}),
                      'timestamp_modified': now},
        )

    # Free-capacity signal: a worker can't poll AND process at the same time
    # (_process_work blocks the loop). So a poll from machine X means X has
    # no in-flight work — any of X's prior claims are stale (lost work,
    # crashed result POST, etc.). Unclaim them and reset to 'staged' so
    # they can be picked up again (possibly by this same worker on next poll).
    stale_claims = Entry.objects.filter(
        data__worker_claimed_by=machine_id,
        deleted_at__isnull=True,
    )
    for stale in stale_claims:
        sdata = stale.data if isinstance(stale.data, dict) else {}
        # Don't reclaim entries already done/blocked — only in-flight ones
        if stale.status not in ('active', None):
            continue
        sdata.pop('worker_claimed_by', None)
        sdata.pop('worker_claimed_at', None)
        stale.data = sdata
        stale.timestamp_modified = now
        stale.save(update_fields=['data', 'timestamp_modified'])
        # Reset base entry's {model}_status from 'active' back to 'staged'
        base_eid = sdata.get('base_entry_id')
        model = sdata.get('model')
        if base_eid and model:
            base = Entry.objects.filter(
                data__entry_id=base_eid, deleted_at__isnull=True,
            ).first()
            if base:
                bd = base.data if isinstance(base.data, dict) else {}
                if bd.get(f'{model}_status') == 'active':
                    bd[f'{model}_status'] = 'staged'
                    base.data = bd
                    base.timestamp_modified = now
                    base.save(update_fields=['data', 'timestamp_modified'])
        logger.info("worker_poll: reclaimed stale claim on %s by %s "
                    "(worker has free capacity)", stale.id, machine_id)

    deadline = now + WORKER_POLL_HOLD_SECONDS
    while True:
        entry = _claim_worker_entry(machine_id, capabilities)
        if entry:
            edata = entry.data if isinstance(entry.data, dict) else {}
            # work_type is informational on the wire — the Mac worker uses
            # it for log labels and the server uses it for AppLog. Derive
            # from data.source so codoc/external jobs arrive labelled as
            # something more useful than 'generic'. Keep 'generic' as the
            # fallback for any future external source we don't recognise.
            _src = edata.get('source')
            if _src == 'multimodel':
                work_type = 'research'
            elif _src == 'corun-ai':
                work_type = 'codoc'
            else:
                work_type = 'generic'
            work = {
                "entry_id": str(entry.id),
                "work_type": work_type,
                "model": edata.get('worker_target'),
                "prompt": edata.get('worker_prompt', ''),
                "timeout_sec": int(edata.get('worker_timeout_sec', 3600)),
                "base_entry_id": edata.get('base_entry_id'),
            }
            AppLog.objects.create(
                source='worker',
                timestamp=tz.now(),
                level=logging.INFO,
                levelname='INFO',
                message=f"Dispatched {work_type} work to {machine_id}: "
                        f"entry={entry.id} model={work['model']}",
                extra_data={'entry_id': str(entry.id), 'machine_id': machine_id,
                            'model': work['model']},
            )
            return JsonResponse({"status": "ok", "work": work})

        remaining = deadline - time.time()
        if remaining <= 0:
            return JsonResponse({"status": "ok", "work": None})
        time.sleep(min(WORKER_POLL_INTERVAL, remaining))


@csrf_exempt
@require_http_methods(["POST"])
def worker_result(request):
    """Receive result from a remote worker and finalize the entry.

    Request body:
        {
            "machine_id": "<uuid>",
            "entry_id": "<uuid>",
            "status": "done" | "failed",
            "result": "<inference output>",
            "error": "<error text if failed>",
            "duration_sec": <int>
        }

    For research entries, triggers research_model_complete which updates the
    base entry's per-model status and dispatches synthesis if all models are
    done. For other work types, just updates the entry.
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    machine_id = body.get('machine_id')
    entry_id = body.get('entry_id')
    status = body.get('status')
    result = body.get('result', '') or ''
    error = body.get('error') or ''
    duration_sec = int(body.get('duration_sec', 0) or 0)

    if not (machine_id and entry_id and status):
        return JsonResponse(
            {"error": "machine_id, entry_id, status required"}, status=400)
    if status not in ('done', 'failed'):
        return JsonResponse(
            {"error": "status must be 'done' or 'failed'"}, status=400)

    entry = Entry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if not entry:
        return JsonResponse({"error": "entry not found"}, status=404)

    edata = entry.data if isinstance(entry.data, dict) else {}

    # Verify this worker holds the claim (optional but helps catch bugs)
    claimed_by = edata.get('worker_claimed_by')
    if claimed_by and claimed_by != machine_id:
        logger.warning(
            "worker_result: %s reported by %s but claimed by %s",
            entry_id, machine_id, claimed_by)
        # Don't hard-fail — the worker did the work, accept the result anyway

    # Preserve the topic line (entry content at dispatch time).
    # For research entries content == topic; for other work types it's whatever
    # the dispatcher set. Rewrite as topic + result on success.
    topic = entry.content or ''

    if status == 'done':
        entry.content = f"{topic}\n\n{result}" if result else topic
        entry.status = 'done'
    else:
        entry.content = f"{topic}\n\nERROR: {error}"
        entry.status = 'blocked'

    # Clear worker tracking — prompt is no longer needed
    for k in ('worker_prompt', 'worker_claimed_by', 'worker_claimed_at',
              'worker_target', 'worker_staged_at', 'worker_timeout_sec'):
        edata.pop(k, None)
    edata['worker_duration_sec'] = duration_sec
    edata['worker_machine_id'] = machine_id
    # Save raw result/error on the entry data so external callers (e.g.
    # api_work_result) can return it cleanly without parsing entry.content,
    # which embeds the topic prefix.
    if status == 'done':
        edata['worker_result'] = result
        edata.pop('worker_error', None)
    else:
        edata['worker_error'] = error
        edata.pop('worker_result', None)
    entry.data = edata
    entry.timestamp_modified = time.time()
    entry.is_dirty = 1
    entry.save(update_fields=['content', 'status', 'data',
                               'timestamp_modified', 'is_dirty'])

    from django.utils import timezone as tz
    log_level = logging.INFO if status == 'done' else logging.ERROR
    AppLog.objects.create(
        source='worker',
        timestamp=tz.now(),
        level=log_level,
        levelname=logging.getLevelName(log_level),
        message=f"Worker {machine_id} {status} entry {entry_id} in {duration_sec}s"
                + (f": {error[:200]}" if status == 'failed' else ''),
        extra_data={'entry_id': entry_id, 'machine_id': machine_id,
                    'status': status, 'duration_sec': duration_sec},
    )

    # Research entries: update base tracking and check for synthesis trigger.
    # Failed counts as done for synthesis purposes (with 4 models dispatched,
    # one or two failures still leaves a meaningful synthesis).
    if edata.get('source') == 'multimodel' and edata.get('model'):
        try:
            from tjai_app.action_runner import research_model_complete
            research_model_complete(
                entry,
                terminal_status='done' if status == 'done' else 'failed',
            )
        except Exception as e:
            logger.error("worker_result: research_model_complete failed: %s", e)

    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_http_methods(["POST"])
def api_work_submit(request):
    """External job submission for the remote-worker pipeline.

    Allows external apps (e.g. corun-ai) to submit a unit of work that will
    be picked up by an existing remote worker via the same long-poll path
    as research jobs. The dispatcher and worker side are unchanged — this
    endpoint just stages an Entry that matches a worker capability.

    Request body:
        {
            "capability": "gemma4" | "gemma4-fast" | ...,
            "prompt": "<text>",
            "timeout_sec": <int, optional, default 3600>,
            "source": "<string, optional>",      # caller identifier
            "label":  "<string, optional>"        # caller's job label
        }

    Response:
        {"status": "ok", "entry_id": "<uuid>"}
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    capability = (body.get('capability') or '').strip()
    prompt = body.get('prompt') or ''
    if not capability:
        return JsonResponse({"error": "capability required"}, status=400)
    if not prompt:
        return JsonResponse({"error": "prompt required"}, status=400)
    if capability not in WORKER_CAPABILITIES:
        return JsonResponse(
            {"error": f"unknown capability: {capability!r}. "
                      f"known: {sorted(WORKER_CAPABILITIES)}"},
            status=400)

    try:
        timeout_sec = int(body.get('timeout_sec') or 3600)
    except (TypeError, ValueError):
        return JsonResponse({"error": "timeout_sec must be int"}, status=400)

    source = (body.get('source') or '').strip() or None
    label = (body.get('label') or '').strip() or None

    now = time.time()
    edata = {
        'worker_target': capability,
        'worker_prompt': prompt,
        'worker_staged_at': now,
        'worker_timeout_sec': timeout_sec,
    }
    if source:
        edata['source'] = source
    if label:
        edata['external_label'] = label

    entry = Entry.objects.create(
        id=str(uuid.uuid7()),
        content=label or f'{capability} work from {source or "external"}',
        kind='memory',
        context_id='tjai',
        status='active',
        data=edata,
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )

    from django.utils import timezone as tz
    AppLog.objects.create(
        source='worker',
        timestamp=tz.now(),
        level=logging.INFO,
        levelname='INFO',
        message=f"Staged {capability} work entry={entry.id} "
                f"source={source} label={label}",
        extra_data={'entry_id': str(entry.id), 'capability': capability,
                    'source': source, 'label': label},
    )

    return JsonResponse({"status": "ok", "entry_id": str(entry.id)})


@csrf_exempt
@require_http_methods(["GET", "DELETE"])
def api_work_result(request, entry_uuid):
    """Poll for / dispose of an externally-submitted work entry.

    GET: returns the current state of the entry.
        {
          "status": "queued" | "running" | "done" | "failed",
          "result": "<text>",          # if done
          "error":  "<text>",          # if failed
          "duration_sec": <int>,
          "claimed_by": "<machine_id>",
          "claimed_at_ago": <seconds>,
          "staged_at_ago": <seconds>,
          "label": "<external_label>"
        }
        Status mapping:
            entry.status='active' + no claim → queued
            entry.status='active' + claim    → running
            entry.status='done'              → done
            entry.status='blocked'           → failed

    DELETE: soft-deletes the entry. Callers should issue this after
    successfully retrieving a done/failed result so external work entries
    do not accumulate.
    """
    entry = Entry.objects.filter(id=str(entry_uuid),
                                 deleted_at__isnull=True).first()
    if not entry:
        return JsonResponse({"error": "entry not found"}, status=404)

    if request.method == 'DELETE':
        entry.deleted_at = time.time()
        entry.is_dirty = 1
        entry.save(update_fields=['deleted_at', 'is_dirty'])
        return JsonResponse({"status": "ok"})

    edata = entry.data if isinstance(entry.data, dict) else {}
    now = time.time()

    if entry.status == 'done':
        state = 'done'
    elif entry.status == 'blocked':
        state = 'failed'
    elif edata.get('worker_claimed_by'):
        state = 'running'
    else:
        state = 'queued'

    resp = {
        'status': state,
        'label': edata.get('external_label'),
        'duration_sec': int(edata.get('worker_duration_sec') or 0),
    }
    if state == 'done':
        resp['result'] = edata.get('worker_result', '')
    elif state == 'failed':
        resp['error'] = edata.get('worker_error', '')

    claimed_at = edata.get('worker_claimed_at')
    if claimed_at:
        try:
            resp['claimed_by'] = edata.get('worker_claimed_by')
            resp['claimed_at_ago'] = int(now - float(claimed_at))
        except (TypeError, ValueError):
            pass
    staged_at = edata.get('worker_staged_at')
    if staged_at:
        try:
            resp['staged_at_ago'] = int(now - float(staged_at))
        except (TypeError, ValueError):
            pass

    return JsonResponse(resp)


@csrf_exempt
@require_http_methods(["POST"])
def api_command(request):
    """
    Execute a command on the server.

    No @login_required by design. The `tj` CLI/daemon (tj/server.py
    send_command) POSTs here with no credential for set_sysconfig /
    get_sysconfig (sync interval, run-action state). Adding @login_required
    breaks the CLI.

    Request body:
    {
        "command": "set_sysconfig",
        "key": "sync_interval_seconds",
        "value": "30"
    }

    Supported commands:
        set_sysconfig: Set a sysconfig key/value pair
        get_sysconfig: Get all sysconfig values

    Response:
    {
        "status": "ok",
        "result": {...}
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    command = data.get("command")
    if not command:
        return JsonResponse({"error": "command required"}, status=400)

    if command == "set_sysconfig":
        key = data.get("key")
        value = data.get("value")
        if not key or value is None:
            return JsonResponse({"error": "key and value required"}, status=400)

        SysConfig.objects.update_or_create(
            key=key,
            defaults={
                "value": str(value),
                "timestamp_modified": time.time(),
            }
        )
        return JsonResponse({"status": "ok", "result": {key: value}})

    elif command == "get_sysconfig":
        sysconfig = {
            cfg["key"]: cfg["value"]
            for cfg in SysConfig.objects.values("key", "value")
        }
        return JsonResponse({"status": "ok", "result": sysconfig})

    elif command == "increment_json":
        key = data.get("key")
        field = data.get("field")
        if not key or not field:
            return JsonResponse({"error": "key and field required"}, status=400)
        sc, _ = SysConfig.objects.get_or_create(
            key=key, defaults={"value": "{}", "timestamp_modified": time.time()})
        try:
            obj = json.loads(sc.value)
        except (json.JSONDecodeError, TypeError):
            obj = {}
        obj[field] = obj.get(field, 0) + 1
        sc.value = json.dumps(obj)
        sc.timestamp_modified = time.time()
        sc.save(update_fields=["value", "timestamp_modified"])
        return JsonResponse({"status": "ok", "result": obj})

    else:
        return JsonResponse({"error": f"Unknown command: {command}"}, status=400)


@csrf_exempt
@require_http_methods(["DELETE"])
def api_delete_entry(request, entry_id):
    """
    Soft delete an entry by ID.

    Response:
        {"status": "ok", "deleted": {...entry details...}}
        {"error": "..."} on failure
    """
    # Handle AppLog records (dashboard shows them with id="log-<pk>")
    if entry_id.startswith('log-'):
        log_pk = entry_id[4:]
        log = AppLog.objects.filter(id=log_pk).first()
        if not log:
            return JsonResponse({"error": f"Log '{entry_id}' not found"}, status=404)
        log.delete()
        return JsonResponse({"status": "ok", "deleted": {"id": entry_id, "kind": "log"}})

    hard = request.GET.get('hard') == '1'
    if hard:
        entry = Entry.objects.filter(id=entry_id).first()
        if not entry:
            return JsonResponse({"error": f"Entry '{entry_id}' not found"}, status=404)
        entry.tags.all().delete()
        entry.versions.all().delete()
        entry.delete()
        return JsonResponse({"status": "ok", "deleted": {"id": entry_id, "kind": "hard"}})

    entry = Entry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if not entry:
        entry = Entry.objects.filter(
            data__entry_id=entry_id, deleted_at__isnull=True
        ).first()
    if not entry:
        return JsonResponse({"error": f"Entry '{entry_id}' not found or already deleted"}, status=404)

    from .models import snapshot_entry
    snapshot_entry(entry, changed_by='delete')

    now = time.time()
    entry.deleted_at = now
    entry.timestamp_modified = now
    entry.is_dirty = 1
    entry.save(update_fields=['deleted_at', 'timestamp_modified', 'is_dirty'])

    return JsonResponse({
        "status": "ok",
        "deleted": {
            "id": entry.id,
            "content_preview": entry.content[:100] + '...' if len(entry.content) > 100 else entry.content,
            "kind": entry.kind,
            "context": entry.context.name if entry.context else None,
        }
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_archive_entry(request, entry_id):
    """Archive an entry by setting status='archive', including from Trash."""
    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    keep_time = data.get('keep_time') is True
    include_deleted = request.GET.get('from_trash') == '1'
    qs = Entry.objects.all() if include_deleted else Entry.objects.filter(deleted_at__isnull=True)
    entry = qs.filter(id=entry_id).first()
    if not entry:
        entry = qs.filter(data__entry_id=entry_id).first()
    if not entry:
        return JsonResponse({"error": f"Entry '{entry_id}' not found"}, status=404)
    if entry.status == 'archive' and entry.deleted_at is None:
        return JsonResponse({"status": "ok", "archived": {"id": entry.id}, "noop": "already archived"})

    from .models import snapshot_entry
    snapshot_entry(entry, changed_by='archive')

    now = time.time()
    entry.deleted_at = None
    entry.status = 'archive'
    entry.is_dirty = 1
    update_fields = ['deleted_at', 'status', 'is_dirty']
    if not keep_time:
        entry.timestamp_modified = now
        update_fields.append('timestamp_modified')
    entry.save(update_fields=update_fields)

    return JsonResponse({
        "status": "ok",
        "archived": {
            "id": entry.id,
            "content_preview": entry.content[:100] + '...' if len(entry.content) > 100 else entry.content,
            "kind": entry.kind,
            "context": entry.context.name if entry.context else None,
        }
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_restore_entry(request, entry_id):
    """Restore a trashed entry to the normal dashboard."""
    entry = Entry.objects.filter(id=entry_id, deleted_at__isnull=False).first()
    if not entry:
        return JsonResponse({"error": f"Entry '{entry_id}' not found in Trash"}, status=404)

    from .models import snapshot_entry
    snapshot_entry(entry, changed_by='restore')

    now = time.time()
    entry.deleted_at = None
    entry.timestamp_modified = now
    entry.is_dirty = 1
    entry.save(update_fields=['deleted_at', 'timestamp_modified', 'is_dirty'])

    return JsonResponse({
        "status": "ok",
        "restored": {
            "id": entry.id,
            "content_preview": entry.content[:100] + '...' if len(entry.content) > 100 else entry.content,
            "kind": entry.kind,
            "context": entry.context.name if entry.context else None,
        }
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_empty_trash(request):
    """Permanently delete all entries already in Trash."""
    trashed = list(Entry.objects.filter(deleted_at__isnull=False).only('id'))
    count = len(trashed)
    for entry in trashed:
        entry.tags.all().delete()
        entry.versions.all().delete()
        entry.delete()
    return JsonResponse({"status": "ok", "deleted": count})


def login_view(request):
    """Custom login page."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    form_errors = False
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.POST.get('next') or request.GET.get('next') or 'dashboard'
            return redirect(next_url)
        else:
            form_errors = True

    return render(request, 'tjai_app/login.html', {
        'form': type('Form', (), {'errors': form_errors})(),
        'next': request.GET.get('next', ''),
    })


def auto_login(request):
    """Auto-login for KozyKorner family users. Verifies API key, logs in as 'family', redirects."""
    key = request.GET.get('key', '')
    next_url = request.GET.get('next', '/tjai/dashboard/')

    try:
        api_key = SysConfig.objects.get(key='gmail_addon_api_key').value
    except SysConfig.DoesNotExist:
        return JsonResponse({"error": "Not configured"}, status=503)

    if key != api_key:
        return redirect('login')

    from django.contrib.auth.models import User
    user = User.objects.filter(username='family').first()
    if not user:
        return JsonResponse({"error": "Family account not found"}, status=500)

    login(request, user)
    return redirect(next_url)


def public_home(request):
    """Render the public landing page."""
    return render(request, 'tjai_app/public_home.html')


def logout_view(request):
    """Log out the user and redirect to login."""
    logout(request)
    return redirect('login')


@login_required
def dashboard(request):
    """Render the dashboard HTML page."""
    if request.GET.get('status') == 'archive' and request.GET.get('view') != 'archive':
        params = request.GET.copy()
        params['view'] = 'archive'
        del params['status']
        return HttpResponseRedirect(f"?{params.urlencode()}")
    named_sort = SysConfig.objects.filter(
        key='dashboard_named_sort'
    ).values_list('value', flat=True).first()
    if named_sort not in ('alpha', 'mod'):
        named_sort = 'alpha'
    return render(request, 'tjai_app/dashboard.html', {
        'is_archive': request.GET.get('view') == 'archive',
        'is_dialog': request.GET.get('view') == 'dialog',
        'is_trash': request.GET.get('deleted') == '1',
        'current_dialog_context': CURRENT_DIALOG_CONTEXT,
        'dialog_contexts_json': json.dumps(list(DIALOG_CONTEXTS)),
        'named_sort': named_sort,
    })


def _relation_counts_for_entry_ids(entry_ids):
    """Return relation counts for current-page entries using grouped queries."""
    counts = Counter()
    if not entry_ids:
        return counts
    for row in Relation.objects.filter(entry1_id__in=entry_ids).values('entry1_id').annotate(n=Count('id')):
        counts[row['entry1_id']] += row['n']
    for row in Relation.objects.filter(entry2_id__in=entry_ids).values('entry2_id').annotate(n=Count('id')):
        counts[row['entry2_id']] += row['n']
    return counts


def _expanded_relation_ids(request, entry_ids):
    """Return URL-expanded relation ids that are present on this page."""
    page_ids = {str(eid) for eid in entry_ids}
    requested = {
        rel_id for rel_id in request.GET.get('rel', '').split(',')
        if rel_id
    }
    return requested & page_ids


def _relations_for_expanded_entry_ids(expanded_ids):
    """Fetch relation detail only for rows the dashboard will render expanded."""
    if not expanded_ids:
        return {}
    from . import services
    return {
        entry_id: services._get_relations_for_entry(entry_id, max_content_length=120)
        for entry_id in expanded_ids
    }


def _entry_ids_with_relations():
    """Return a queryset of entry ids that appear on either side of a relation."""
    return Entry.objects.filter(
        Q(relations_as_entry1__isnull=False) | Q(relations_as_entry2__isnull=False)
    ).values_list('id', flat=True).distinct()


@login_required
def versions_page(request):
    """Flat reverse-chron list of all entry versions."""
    from .models import EntryVersion
    versions = EntryVersion.objects.select_related('entry').order_by('-timestamp')[:1000]
    items = []
    for v in versions:
        entry = v.entry
        data = entry.data if isinstance(entry.data, dict) else {}
        entry_id = data.get('entry_id', '')
        entry_name = entry.name or ''
        entry_label = f'@{entry_name}' if entry_name else entry_id or str(entry.id)[:8]
        items.append({
            'version_num': v.version_num,
            'datetime': fmt_datetime(v.timestamp),
            'changed_by': v.changed_by,
            'line_count': v.content.count('\n') + 1 if v.content else 0,
            'preview': v.content[:120].replace('\n', ' ') if v.content else '',
            'entry_label': entry_label,
            'entry_url': f'/tjai/entry/?entry_id={entry_id}' if entry_id else f'/tjai/entry/?uuid={entry.id}',
            'edit_url': f'/tjai/entry/?uuid={entry.id}&version={v.id}&edit=1',
        })
    return render(request, 'tjai_app/versions.html', {'items_json': json.dumps(items)})


@login_required
def diary_page(request):
    """Render the Diary overview page."""
    return render(request, 'tjai_app/diary.html')


@login_required
def api_diary_entries(request):
    """Return @Underway entry and diary journal entries as JSON."""
    from .models import Entry, Tag

    def render_entry(entry):
        body_lines = entry.content.split('\n')
        first_line = body_lines[0] if body_lines else ''
        body_text = '\n'.join(body_lines[1:]).strip() if len(body_lines) > 1 else ''
        data = entry.data if isinstance(entry.data, dict) else {}
        fmt = data.get('format') or 'md'
        md_exts = ['nl2br', 'tables', 'fenced_code'] if fmt == 'txt' else ['tables', 'fenced_code']
        content_html = _render_markdown(body_text, extensions=md_exts)
        def _wl(m):
            ref = m.group(1)
            if ref.startswith('http://') or ref.startswith('https://'):
                return f'<a href="{ref}">{ref}</a>'
            if ref.startswith('@'):
                return f'<a href="/tjai/entry/?name={ref[1:]}">{ref}</a>'
            return f'<a href="/tjai/entry/?entry_id={ref}">{ref}</a>'
        content_html = re.sub(r'\[\[([^\]]+)\]\]', _wl, content_html)
        content_html = _linkify_rendered_html(content_html)
        entry_id = data.get('entry_id', '')
        return {
            'id': str(entry.id),
            'entry_id': entry_id,
            'first_line': first_line,
            'content_html': content_html,
            'kind': entry.kind,
            'modified': entry.timestamp_modified,
        }

    result = {'underway': None, 'diary_entries': []}

    # @Underway
    underway = Entry.objects.filter(name='Underway', deleted_at__isnull=True).first()
    if underway:
        result['underway'] = render_entry(underway)

    # Diary entries only (entry_id starts with 'diary-'), not daily synopsis
    diary_qs = Entry.objects.filter(
        context_id='diary', kind='journal', deleted_at__isnull=True,
        data__entry_id__startswith='diary-',
    ).order_by('-timestamp_modified')[:1000]
    result['diary_entries'] = [render_entry(e) for e in diary_qs]

    return JsonResponse(result)


@login_required
def api_offline_material_cache_manifest(request):
    """Return first-class diary/daily/weekly URLs for passive offline caching."""
    items = []
    seen = set()
    groups = {
        'pages': 0,
        'dashboard': 0,
        'diary': 0,
        'daily': 0,
        'activity': 0,
        'agents': 0,
        'ai': 0,
        'goals': 0,
        'journals': 0,
        'named': 0,
        'readme': 0,
        'rss': 0,
        'system': 0,
        'todos': 0,
        'relations': 0,
        'workday': 0,
        'workweek': 0,
        'highlights': 0,
        'underway': 0,
    }

    def add(url, label, kind='api', group='pages'):
        if not url or url in seen:
            return
        seen.add(url)
        items.append({'url': url, 'label': label, 'type': kind, 'group': group})
        groups[group] = groups.get(group, 0) + 1

    add('/tjai/diary/', 'Diary page', 'page', 'pages')
    add('/tjai/api/diary/entries', 'Diary entries API', 'api', 'diary')
    add('/tjai/dashboard/', 'Dashboard page', 'page', 'dashboard')
    add('/tjai/dashboard/?view=dialog', 'Dialog dashboard page', 'page', 'dashboard')
    add('/tjai/dashboard/?view=archive', 'Archive dashboard page', 'page', 'dashboard')
    add('/tjai/dashboard/?deleted=1', 'Trash dashboard page', 'page', 'dashboard')
    add('/tjai/api/dashboard/calendar', 'Dashboard calendar API', 'api', 'dashboard')
    add('/tjai/api/dashboard/named', 'Dashboard named API', 'api', 'dashboard')
    add('/tjai/api/dashboard/status', 'Dashboard default status API', 'api', 'dashboard')
    add('/tjai/api/dashboard/status?view=dialog', 'Dashboard dialog status API', 'api', 'dashboard')
    add('/tjai/api/dashboard/status?view=archive', 'Dashboard archive status API', 'api', 'dashboard')
    add('/tjai/api/dashboard/status?deleted=1', 'Dashboard trash status API', 'api', 'dashboard')
    add('/tjai/api/dialog/daily-counts', 'Dashboard dialog counts API', 'api', 'dashboard')
    add('/tjai/assessment/', 'AI assessment page', 'page', 'ai')
    add('/tjai/api/assessment/dates?assessor=gemini', 'AI dates gemini', 'api', 'ai')
    add('/tjai/api/assessment/dashboard?assessor=gemini', 'AI dashboard gemini', 'api', 'ai')
    add('/tjai/git/', 'Git page', 'page', 'activity')
    add('/tjai/api/git/data', 'Git data API', 'api', 'activity')
    add('/tjai/dev/', 'Dev page', 'page', 'activity')
    add('/tjai/api/dev/data', 'Dev data API', 'api', 'activity')
    add('/tjai/agent-log/', 'Agent log page', 'page', 'agents')
    add('/tjai/api/agent-log?limit=200', 'Agent log API', 'api', 'agents')
    add('/tjai/agent-queue/', 'Agent queue page', 'page', 'agents')
    add('/tjai/api/agent-queue/data', 'Agent queue API', 'api', 'agents')
    add('/tjai/synopsis/', 'Daily synopsis page', 'page', 'pages')
    add('/tjai/api/synopsis/dates', 'Daily synopsis dates API', 'api', 'daily')
    add('/tjai/weekly/', 'Weekly page', 'page', 'pages')
    add('/tjai/this-week/', 'This workweek page', 'page', 'pages')
    add('/tjai/goals/', 'Goals page', 'page', 'goals')
    add('/tjai/api/goals/data?include_done=1', 'Goals API', 'api', 'goals')
    add('/tjai/context/recipe/', 'Recipes page', 'page', 'pages')
    add('/tjai/context/poetry/', 'Poetry page', 'page', 'pages')
    add('/tjai/picks/', 'Picks page', 'page', 'readme')
    add('/tjai/api/picks/data', 'Picks data API', 'api', 'readme')
    add('/tjai/rss/', 'RSS page', 'page', 'rss')
    add('/tjai/api/rss/data', 'RSS data API', 'api', 'rss')
    add('/tjai/readme/', 'ReadMe page', 'page', 'readme')
    add('/tjai/api/readme/data', 'ReadMe data API', 'api', 'readme')
    add('/tjai/system/', 'System page', 'page', 'system')
    add('/tjai/api/system/status', 'System menu status API', 'api', 'system')
    add('/tjai/api/system/data', 'System data API', 'api', 'system')

    def add_entry_material(entry, group):
        data = entry.data if isinstance(entry.data, dict) else {}
        eid = data.get('entry_id')
        if eid:
            quoted_eid = quote(eid, safe='')
            page_url = f'/tjai/entry/?entry_id={quoted_eid}'
            add(f'/tjai/entry/{quoted_eid}/', eid, 'page', group)
        else:
            page_url = f'/tjai/entry/?uuid={entry.id}'
            add(f'/tjai/entry/{entry.id}/', str(entry.id), 'page', group)
        label = eid or str(entry.id)
        add(page_url, label, 'page', group)
        add(f'/tjai/api/entry/{entry.id}/relations', label + ' relations', 'api', 'relations')
        if entry.kind == 'goal':
            add(f'/tjai/api/goals/detail?id={entry.id}', label + ' goal detail', 'api', 'goals')

    underway = Entry.objects.filter(name='Underway', deleted_at__isnull=True).first()
    if underway:
        data = underway.data if isinstance(underway.data, dict) else {}
        eid = data.get('entry_id')
        url = f'/tjai/entry/?entry_id={quote(eid)}' if eid else f'/tjai/entry/?uuid={underway.id}'
        add(url, '@Underway entry', 'page', 'underway')

    highlights = Entry.objects.filter(
        data__entry_id='work-hours-highlights',
        deleted_at__isnull=True,
    ).first()
    if highlights:
        add('/tjai/entry/?entry_id=work-hours-highlights', 'Work highlights', 'page', 'highlights')

    goal_todo_entries = list(Entry.objects.filter(
        kind__in=('goal', 'todo'),
        deleted_at__isnull=True,
    ).only('id', 'kind', 'data'))
    for entry in goal_todo_entries:
        add_entry_material(entry, 'goals' if entry.kind == 'goal' else 'todos')

    named_entries = Entry.objects.filter(
        deleted_at__isnull=True,
        name__isnull=False,
    ).exclude(name='').only('id', 'kind', 'data')
    for entry in named_entries:
        add_entry_material(entry, 'named')

    journal_entries = Entry.objects.filter(
        kind='journal',
        deleted_at__isnull=True,
    ).only('id', 'kind', 'data')
    for entry in journal_entries:
        add_entry_material(entry, 'journals')

    goal_todo_ids = [str(entry.id) for entry in goal_todo_entries]
    if goal_todo_ids:
        relation_rows = Relation.objects.filter(
            Q(entry1_id__in=goal_todo_ids) | Q(entry2_id__in=goal_todo_ids)
        ).values_list('entry1_id', 'entry2_id')
        related_ids = {entry_id for row in relation_rows for entry_id in row}
        related_entries = Entry.objects.filter(
            id__in=related_ids,
            deleted_at__isnull=True,
        ).only('id', 'kind', 'data')
        for entry in related_entries:
            add_entry_material(entry, 'relations')

    diary_entries = Entry.objects.filter(
        context_id='diary',
        kind='journal',
        deleted_at__isnull=True,
        data__entry_id__startswith='diary-',
    ).order_by('-timestamp_modified')[:1000]
    for entry in diary_entries:
        eid = (entry.data or {}).get('entry_id', '')
        if eid:
            add(f'/tjai/entry/?entry_id={quote(eid)}&edit=1', eid, 'page', 'diary')

    daily_tag_ids = Tag.objects.filter(tag_name='daily').values_list('entry_id', flat=True)
    daily_entries = Entry.objects.filter(
        kind='journal',
        deleted_at__isnull=True,
        id__in=daily_tag_ids,
    ).order_by('-data__event_date')
    for entry in daily_entries:
        eid = (entry.data or {}).get('entry_id', '')
        if eid and eid.startswith('daily-'):
            q = quote(eid)
            add(f'/tjai/synopsis/?entry_id={q}', eid + ' synopsis page', 'page', 'daily')
            add(f'/tjai/api/synopsis/content?entry_id={q}', eid, 'api', 'daily')
            add(f'/tjai/entry/?entry_id={q}', eid + ' entry', 'page', 'daily')

    assessment_entries = Entry.objects.filter(
        data__entry_id__startswith='assessment-',
        kind='memory',
        deleted_at__isnull=True,
    ).exclude(data__entry_id__contains='-prompt').only('id', 'kind', 'data')
    for entry in assessment_entries:
        eid = (entry.data or {}).get('entry_id', '')
        if eid:
            q = quote(eid)
            add(f'/tjai/api/assessment/content?entry_id={q}', eid + ' assessment data', 'api', 'ai')
            add(f'/tjai/entry/?entry_id={q}', eid + ' entry', 'page', 'ai')

    work_entries = Entry.objects.filter(
        deleted_at__isnull=True,
        data__entry_id__regex=r'^(workday|workweek)_\d{8}$',
    ).order_by('-data__entry_id')[:2000]
    for entry in work_entries:
        eid = (entry.data or {}).get('entry_id', '')
        if not eid:
            continue
        group = 'workweek' if eid.startswith('workweek_') else 'workday'
        add(f'/tjai/entry/?entry_id={quote(eid)}', eid, 'page', group)
        if group == 'workweek':
            add(f'/tjai/workweek/{eid.removeprefix("workweek_")}/', eid + ' route', 'page', group)
        else:
            add(f'/tjai/workday/{eid.removeprefix("workday_")}/', eid + ' route', 'page', group)

    return JsonResponse({
        'items': items,
        'groups': groups,
        'generated_at': time.time(),
    })


@login_required
def dashboard_calendar(request):
    """Return calendar data as JSON for dashboard."""
    now = time.time()

    tz = get_app_tz()
    timezone_name = str(tz)

    # Support lazy loading: ?before=TIMESTAMP loads older entries
    before_ts = request.GET.get('before')
    if before_ts:
        try:
            end_ts = float(before_ts)
        except ValueError:
            end_ts = now + (60 * 24 * 60 * 60)
        start_dt = datetime.fromtimestamp(end_ts, tz=tz) - timedelta(days=30)
        start_ts = start_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    else:
        # Go back 7 days, then to Monday of that week (to show full previous week)
        seven_days_ago = datetime.now(tz) - timedelta(days=7)
        days_since_monday = seven_days_ago.weekday()
        monday_of_prev_week = seven_days_ago - timedelta(days=days_since_monday)
        start_ts = monday_of_prev_week.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        end_ts = now + (60 * 24 * 60 * 60)

    # Query journal entries with event_date in range (exclude annual, handled separately)
    entries = Entry.objects.filter(
        deleted_at__isnull=True,
        kind='journal',
        mmdd__isnull=True,
        data__event_date__gte=start_ts,
        data__event_date__lt=end_ts,
    ).order_by('data__event_date')

    # Calculate today's date key in configured timezone
    now_dt = datetime.now(tz)
    today_date_str = now_dt.strftime('%Y%m%d')

    result = []
    for entry in entries:
        data = entry.data
        if isinstance(data, dict) and 'event_date' in data:
            event_ts = data['event_date']
            # Convert to timezone-aware datetime for formatting
            event_dt = datetime.fromtimestamp(event_ts, tz=tz)

            # Pre-format all date/time strings server-side
            date_key = event_dt.strftime('%Y%m%d')

            entry_id = data.get('entry_id', '')
            # Only show today's daily synopsis/diary in calendar. Older diary
            # entries belong on the Diary page.
            if (
                (entry_id.startswith('daily-') or entry_id.startswith('diary-'))
                and date_key != today_date_str
            ):
                continue
            date_display = event_dt.strftime('%a %b %-d')
            is_allday = entry_id.startswith('daily-') or entry_id.startswith('diary-') or not (event_dt.hour or event_dt.minute)
            time_display = None if is_allday else event_dt.strftime('%H:%M')
            week_num = event_dt.isocalendar()[1]
            week_start = event_dt - timedelta(days=event_dt.weekday())
            week_start_key = week_start.strftime('%Y%m%d')

            result.append({
                'id': str(entry.id),
                'content': entry.content,
                'event_date': event_ts,
                'date_key': date_key,
                'date_display': date_display,
                'time_display': time_display,
                'week_num': week_num,
                'week_start_key': week_start_key,
                'context': entry.context_id,
                'data': data,
            })

    # Inject annual events
    from .services import _query_annual_events
    # start_ts is midnight-floored in both the before and default branches;
    # derive the annual-event window start from it (monday_of_prev_week is
    # only assigned in the default branch).
    start_dt = datetime.fromtimestamp(start_ts, tz=tz)
    end_dt = datetime.fromtimestamp(end_ts, tz=tz)
    today_mmdd = now_dt.month * 100 + now_dt.day
    seen_ids = {r['id'] for r in result}
    for entry in _query_annual_events(start_dt, end_dt, today_mmdd):
        if str(entry.id) in seen_ids:
            continue
        annual_month = entry.mmdd // 100
        annual_day = entry.mmdd % 100
        try:
            projected_dt = now_dt.replace(month=annual_month, day=annual_day, hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            continue
        date_key = projected_dt.strftime('%Y%m%d')
        date_display = projected_dt.strftime('%a %b %-d')
        week_num = projected_dt.isocalendar()[1]
        week_start = projected_dt - timedelta(days=projected_dt.weekday())
        week_start_key = week_start.strftime('%Y%m%d')

        result.append({
            'id': str(entry.id),
            'content': entry.content,
            'event_date': projected_dt.timestamp(),
            'date_key': date_key,
            'date_display': date_display,
            'time_display': None,
            'week_num': week_num,
            'week_start_key': week_start_key,
            'context': entry.context_id,
            'data': {'annual': True},
        })

    result.sort(key=lambda r: (r['date_key'], 0 if r['time_display'] is None else 1, r['event_date']))

    return JsonResponse({
        'entries': result,
        'server_time': now,
        'start_ts': start_ts,
        'timezone': timezone_name,
        'today_date': today_date_str,
        'today_date_display': fmt_date(now_dt),
    })


@login_required
def dashboard_status(request):
    """Return status data as JSON for dashboard."""
    now = time.time()

    now_dt = datetime.now(tz=get_app_tz())

    # Format timestamp
    timestamp = fmt_datetime(now_dt)

    # Get current context from most recent entry or default
    context = None
    context_description = None

    # Clock status - find most recent clock start in last 24h
    # Data is now proper JSON (dict), use Django JSON field lookups
    clock_data = None
    cutoff_24h = now - 86400
    clock_entry = Entry.objects.filter(
        kind='journal',
        deleted_at__isnull=True,
        timestamp_created__gte=cutoff_24h,
        data__clock='start'
    ).order_by('-timestamp_created').first()

    if clock_entry and clock_entry.data:
        data = clock_entry.data
        start_time = data.get('event_date', clock_entry.timestamp_created)
        breaks_min = data.get('breaks', 0)
        stop_id = data.get('stop_id')

        if stop_id:
            stop_entry = Entry.objects.filter(id=stop_id).first()
            if stop_entry and stop_entry.data:
                end_time = stop_entry.data.get('event_date', stop_entry.timestamp_created)
            else:
                end_time = now
            stopped = True
        else:
            end_time = now
            stopped = False

        elapsed_min = int((end_time - start_time) / 60)
        work_min = max(0, elapsed_min - breaks_min)

        clock_data = {
            'context': clock_entry.context_id,
            'elapsed_min': elapsed_min,
            'work_min': work_min,
            'breaks_min': breaks_min,
            'stopped': stopped,
        }

    # Today's work sessions
    tz = get_app_tz()
    today_dt = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_dt.timestamp()

    work_sessions = []
    for entry in Entry.objects.filter(
        kind='journal',
        deleted_at__isnull=True,
        timestamp_created__gte=cutoff_24h,
        data__clock='start'
    ).order_by('-timestamp_created'):
        data = entry.data
        if not data:
            continue
        event_ts = data.get('event_date', entry.timestamp_created)
        if event_ts < today_start:
            break  # Hit yesterday, done
        stop_id = data.get('stop_id')
        breaks_min = data.get('breaks', 0)
        if stop_id:
            stop_entry = Entry.objects.filter(id=stop_id).first()
            end_ts = stop_entry.data.get('event_date') if stop_entry and stop_entry.data else now
            stopped = True
        else:
            end_ts = now
            stopped = False
        work_min = max(0, int((end_ts - event_ts) / 60) - breaks_min)
        work_sessions.append({
            'start': event_ts,
            'end': end_ts,
            'work_min': work_min,
            'breaks_min': breaks_min,
            'stopped': stopped,
            'context': entry.context_id,
        })
    work_sessions.reverse()  # Oldest first for display

    # All entries (excluding archived), most recent first
    DASHBOARD_PAGE = 1000
    offset = int(request.GET.get('offset', 0))

    # Server-side filters (applied to DB query before pagination)
    filter_tag = request.GET.get('tag')
    filter_kind = request.GET.get('kind')
    filter_context = request.GET.get('context')
    dialog_view = request.GET.get('view') == 'dialog'
    filter_client = request.GET.get('client')
    filter_model = request.GET.get('model')
    filter_machine = request.GET.get('machine')
    # status: csv multi-select; `_none` token matches NULL/empty status.
    # exclude_status: csv negative selection.
    filter_statuses = [s for s in request.GET.get('status', '').split(',') if s]
    exclude_statuses = [s for s in request.GET.get('exclude_status', '').split(',') if s]
    archive_view = request.GET.get('view') == 'archive'
    filter_date = request.GET.get('date')  # YYYY-MM-DD, filter entries to this day
    filter_from_time = request.GET.get('from_time')  # ISO datetime e.g. 2026-03-04T03:30
    filter_to_time = request.GET.get('to_time')  # ISO datetime e.g. 2026-03-04T05:30
    expand_dialog = request.GET.get('expand_dialog') == '1'
    user_exclude_contexts = [c for c in request.GET.get('exclude_context', '').split(',') if c]
    exclude_contexts = list(user_exclude_contexts)

    # Exclude dialog contexts from the default dashboard view.
    if not filter_context and not dialog_view:
        for ctx in DIALOG_CONTEXTS:
            if ctx not in exclude_contexts:
                exclude_contexts.append(ctx)

    show_deleted = request.GET.get('deleted') == '1'
    if show_deleted:
        base_qs = Entry.objects.filter(deleted_at__isnull=False)
    else:
        base_qs = Entry.objects.filter(deleted_at__isnull=True)

    if filter_kind:
        base_qs = base_qs.filter(kind=filter_kind)
    if dialog_view:
        dialog_ids = Tag.objects.filter(tag_name=DIALOG_TAG).values_list('entry_id', flat=True)
        base_qs = base_qs.filter(id__in=dialog_ids)
    elif filter_context:
        base_qs = base_qs.filter(context_id=filter_context)
    if exclude_contexts:
        base_qs = base_qs.exclude(context_id__in=exclude_contexts)
    if filter_tag:
        filter_tags = [t for t in filter_tag.split(',') if t]
        for t in filter_tags:
            if t == '_none':
                tagged_ids = Tag.objects.values_list('entry_id', flat=True)
                base_qs = base_qs.exclude(id__in=tagged_ids)
            else:
                tagged_ids = Tag.objects.filter(tag_name=t).values_list('entry_id', flat=True)
                base_qs = base_qs.filter(id__in=tagged_ids)
    if filter_machine:
        base_qs = base_qs.filter(data__hostname=filter_machine)
    if filter_client:
        base_qs = base_qs.filter(data__client=filter_client)
    if filter_model:
        base_qs = base_qs.filter(data__model=filter_model)
    if request.GET.get('public') == '1':
        base_qs = base_qs.filter(data__access='public').exclude(context_id__in=['poetry', 'recipe'])
    if request.GET.get('with_relations') == '1':
        base_qs = base_qs.filter(id__in=_entry_ids_with_relations())

    # Daily counts for dialog mode — single raw SQL query for speed
    daily_counts = None
    if (filter_context or dialog_view) and offset == 0:
        try:
            tz = get_app_tz()
            now_local = datetime.now(tz=tz)
            cutoff_ts = (now_local - timedelta(days=14)).timestamp()
            tz_name = str(tz)
            count_contexts = list(DIALOG_CONTEXTS) if dialog_view or filter_context in DIALOG_CONTEXTS else [filter_context]
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT (to_timestamp(timestamp_modified) AT TIME ZONE %s)::date AS day,
                           count(*) AS cnt
                    FROM entries
                    WHERE context = ANY(%s) AND deleted_at IS NULL
                          AND (status IS NULL OR status != 'archive')
                          AND timestamp_modified >= %s
                    GROUP BY day ORDER BY day DESC
                """, [tz_name, count_contexts, cutoff_ts])
                daily_counts = [{'date': row[0].isoformat(), 'count': row[1]} for row in cursor.fetchall()]
        except Exception:
            logger.exception("daily_counts query failed for context=%s view_dialog=%s", filter_context, dialog_view)

    if filter_date:
        tz = get_app_tz()
        day_start = datetime.strptime(filter_date, '%Y-%m-%d').replace(tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        base_qs = base_qs.filter(
            timestamp_modified__gte=day_start.timestamp(),
            timestamp_modified__lt=day_end.timestamp(),
        )

    if filter_from_time:
        try:
            ft = datetime.fromisoformat(filter_from_time)
            if ft.tzinfo is None:
                # Naive string — assume Eastern for backward compat
                ft = ft.replace(tzinfo=get_app_tz())
            base_qs = base_qs.filter(timestamp_modified__gte=ft.timestamp())
        except ValueError:
            pass
    if filter_to_time:
        try:
            tt = datetime.fromisoformat(filter_to_time)
            if tt.tzinfo is None:
                tt = tt.replace(tzinfo=get_app_tz())
            base_qs = base_qs.filter(timestamp_modified__lt=tt.timestamp())
        except ValueError:
            pass

    if not show_deleted:
        if archive_view:
            base_qs = base_qs.filter(status='archive')
        else:
            base_qs = base_qs.exclude(status='archive')

    # Status options are derived from the page population after all non-status
    # filters. Status counts below are derived from the final displayed list.
    status_options_qs = base_qs

    if not show_deleted and (filter_statuses or exclude_statuses):
        base_qs = _apply_status_filter(base_qs, filter_statuses, exclude_statuses)

    base_qs = base_qs.order_by('-timestamp_modified')

    recent = list(base_qs[offset:offset + DASHBOARD_PAGE])

    # Batch fetch tags for all entries
    entry_ids = [e.id for e in recent]
    tags_by_entry = {}
    for t in Tag.objects.filter(entry_id__in=entry_ids):
        tags_by_entry.setdefault(t.entry_id, []).append(t.tag_name)
    relation_counts = _relation_counts_for_entry_ids(entry_ids)
    expanded_relation_ids = _expanded_relation_ids(request, entry_ids)
    expanded_relations = _relations_for_expanded_entry_ids(expanded_relation_ids)

    recent_entries = []
    app_tz = get_app_tz()
    for e in recent:
        lines = e.content.split('\n')
        data = e.data if isinstance(e.data, dict) else None
        line_count = len([l for l in lines if l.strip()])
        # Get tags not already in content
        entry_tags = tags_by_entry.get(e.id, [])
        missing_tags = [t for t in entry_tags if f':{t}' not in e.content]
        event_date_raw = data.get('event_date') if data else None
        try:
            event_date_epoch = float(event_date_raw) if event_date_raw is not None else None
        except (ValueError, TypeError):
            event_date_epoch = None
        # Format event_date with all-day detection
        event_date_display = None
        if event_date_epoch and e.kind == 'journal':
            ev_dt = datetime.fromtimestamp(event_date_epoch, tz=app_tz)
            is_allday = (ev_dt.hour == 0 and ev_dt.minute == 0)
            if ev_dt.year != datetime.now(tz=app_tz).year:
                event_date_display = ev_dt.strftime('%m/%d/%Y')
            elif is_allday:
                event_date_display = ev_dt.strftime('%a %m/%d')
            else:
                event_date_display = ev_dt.strftime('%a %m/%d/%H:%M')
        recent_entries.append({
            'id': e.id,
            'content': e.content if expand_dialog else lines[0],
            'kind': e.kind,
            'context': e.context_id,
            'timestamp': e.timestamp_modified,
            'date_display': fmt_datetime(e.timestamp_modified),
            'line_count': line_count if line_count > 1 else None,
            'name': e.name,
            'entry_id': data.get('entry_id') if data else None,
            'nickname': data.get('nickname') if data else None,
            'event_date': event_date_epoch,
            'event_date_display': event_date_display,
            'hostname': data.get('hostname') if data else None,
            'metadata': data or {},
            'relation_count': relation_counts.get(e.id, 0),
            'relations': expanded_relations.get(str(e.id)),
            'tags': missing_tags,
            'all_tags': entry_tags,
        })

    has_entry_filter = any([
        filter_tag, filter_kind, filter_context, filter_client, filter_model,
        filter_machine, filter_statuses, exclude_statuses, filter_date,
        filter_from_time, filter_to_time, user_exclude_contexts,
        request.GET.get('public') == '1',
        request.GET.get('with_relations') == '1',
    ])
    include_error_logs = (
        offset == 0
        and not dialog_view
        and not show_deleted
        and (not has_entry_filter or filter_kind == 'log')
    )

    # Inject recent ERROR-level logs as pseudo-entries on the default dashboard
    # (or explicit kind=log view), sorted into proper time order with real entries.
    if include_error_logs:
        from django.utils import timezone as _tz
        error_cutoff = _tz.now() - timedelta(hours=24)
        error_logs = AppLog.objects.filter(
            level__gte=40,  # ERROR and above
            timestamp__gte=error_cutoff,
        ).order_by('-timestamp')[:10]
        for log in error_logs:
            log_ts = log.timestamp.astimezone(app_tz)
            log_source = log.source or ''
            recent_entries.append({
                'id': f'log-{log.id}',
                'content': log.message[:200] if log.message else '',
                'kind': 'log',
                'timestamp': log.timestamp.timestamp(),
                'date_display': log_ts.strftime('%a %m/%d/%H:%M'),
                'context': log_source,
                'status': 'blocked',
                'url': f'/tjai/agent-log/?source={log_source}',
                'entry_id': None,
                'name': None,
                'nickname': None,
                'priority': None,
                'event_date_display': None,
                'all_tags': [],
            })
        if error_logs:
            recent_entries.sort(key=lambda e: e.get('timestamp', 0), reverse=True)

    has_more = len(recent_entries) == DASHBOARD_PAGE
    total_count = offset + len(recent_entries) + (1 if has_more else 0)  # approximate, avoid full count

    # If loading more entries (offset > 0), return just entries
    if offset > 0:
        return JsonResponse({
            'recent_entries': recent_entries,
            'has_more': has_more,
            'total_count': total_count,
        })

    timezone_name = str(get_app_tz())

    # Contexts (alpha sorted)
    from django.db.models import Count
    contexts = list(Entry.objects.filter(
        deleted_at__isnull=True, context_id__isnull=False
    ).values_list('context_id', flat=True).distinct().order_by('context_id'))

    # Tags (alpha sorted, excluding context-only tags) with counts for sparse tags
    tag_stats = TagStats.objects.filter(is_context_only=False).order_by('tag_name')
    all_tags = list(tag_stats.values_list('tag_name', flat=True))
    tag_counts = {ts.tag_name: ts.entry_count for ts in tag_stats if ts.entry_count <= 3}

    # Open todos by context
    todos_by_ctx = list(Entry.objects.filter(
        kind='todo',
        deleted_at__isnull=True,
    ).exclude(status='done').values('context_id').annotate(count=Count('id')).order_by('-count'))
    open_todos = [{'context': t['context_id'], 'count': t['count']} for t in todos_by_ctx]

    # Machine sync status - filter out test machines and hostname-less orphans
    # (any machine_id posted to a sync endpoint creates a row; rows with no
    # hostname are uninformative — drop them rather than showing machine_id[:8])
    test_names = {'test', 'test123', 'testhost', 'test-host', 'fake-mac', 'debug'}
    machines_qs = (Machine.objects.filter(is_active=1)
                   .exclude(hostname__in=test_names)
                   .exclude(hostname__isnull=True)
                   .exclude(hostname=''))
    machines = []
    oldest_sync = now
    oldest_machine = None
    for m in machines_qs:
        name = m.hostname
        if name.lower() in test_names:
            continue
        machines.append(name)
        if m.last_sync and m.last_sync < oldest_sync:
            oldest_sync = m.last_sync
            oldest_machine = name
    sync_age_min = int((now - oldest_sync) / 60) if oldest_machine else 0

    # Kind counts (non-archived, non-dialog)
    kind_counts = dict(Entry.objects.filter(
        deleted_at__isnull=True,
    ).exclude(status='archive').exclude(
        context_id__in=DIALOG_CONTEXTS
    ).values('kind').annotate(cnt=Count('id')).values_list('kind', 'cnt'))

    # Status counts reflect the entries actually displayed after status
    # include/exclude filters. Active/excluded statuses are forced in at
    # count 0 so their controls remain visible when the filter empties them.
    status_counts = {}
    status_options = []
    if not show_deleted:
        option_rows = (status_options_qs.values('status')
                       .annotate(cnt=Count('id'))
                       .values_list('status', 'cnt'))
        option_set = set()
        for s, _cnt in option_rows:
            option_set.add(s if s else '_none')
        option_set.update(filter_statuses)
        option_set.update(exclude_statuses)

        rows = (base_qs.values('status')
                .annotate(cnt=Count('id'))
                .values_list('status', 'cnt'))
        for s, cnt in rows:
            key = s if s else '_none'
            status_counts[key] = status_counts.get(key, 0) + cnt
        for s in option_set:
            status_counts.setdefault(s, 0)
        status_options = sorted(option_set)

    dialog_clients = []
    dialog_models = []
    if dialog_view:
        dialog_ids = Tag.objects.filter(tag_name=DIALOG_TAG).values_list('entry_id', flat=True)
        dialog_qs = Entry.objects.filter(
            id__in=dialog_ids,
            deleted_at__isnull=True,
            context_id__in=DIALOG_CONTEXTS,
        )
        dialog_clients = sorted({v for v in dialog_qs.values_list('data__client', flat=True) if v})
        dialog_models = sorted({v for v in dialog_qs.values_list('data__model', flat=True) if v})

    return JsonResponse({
        'timestamp': timestamp,
        'context': context,
        'context_description': context_description,
        'clock': clock_data,
        'work_sessions': work_sessions,
        'recent_entries': recent_entries,
        'has_more': has_more,
        'total_count': total_count,
        'timezone': timezone_name,
        'contexts': contexts,
        'all_tags': all_tags,
        'tag_counts': tag_counts,
        'kind_counts': kind_counts,
        'status_counts': status_counts,
        'status_options': status_options,
        'open_todos': open_todos,
        'machines': machines,
        'oldest_sync': {'machine': oldest_machine, 'age_min': sync_age_min} if oldest_machine else None,
        'daily_counts': daily_counts,
        'dialog_clients': dialog_clients,
        'dialog_models': dialog_models,
    })


@login_required
def dashboard_search(request):
    """Search entries and return JSON in same format as dashboard_status entries."""
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'entries': []})

    from django.contrib.postgres.search import SearchQuery, SearchRank
    try:
        search_query = SearchQuery(q, search_type='websearch', config='english')
    except Exception:
        search_query = SearchQuery(q, config='english')

    show_deleted = request.GET.get('deleted') == '1'
    qs = Entry.objects.filter(
        search_vector=search_query,
        deleted_at__isnull=not show_deleted,
    ).annotate(
        rank=SearchRank('search_vector', search_query, normalization=1, cover_density=True),
    )

    archive_view = request.GET.get('view') == 'archive'
    if not show_deleted:
        if archive_view:
            qs = qs.filter(status='archive')
        else:
            qs = qs.exclude(status='archive')

    # Apply same filters as dashboard_status.
    filter_statuses = [s for s in request.GET.get('status', '').split(',') if s]
    exclude_statuses = [s for s in request.GET.get('exclude_status', '').split(',') if s]
    if not show_deleted and (filter_statuses or exclude_statuses):
        qs = _apply_status_filter(qs, filter_statuses, exclude_statuses)

    filter_kind = request.GET.get('kind')
    if filter_kind:
        qs = qs.filter(kind=filter_kind)

    filter_context = request.GET.get('context', '').strip()
    dialog_view = request.GET.get('view') == 'dialog'
    filter_client = request.GET.get('client')
    filter_model = request.GET.get('model')
    if dialog_view:
        dialog_ids = Tag.objects.filter(tag_name=DIALOG_TAG).values_list('entry_id', flat=True)
        qs = qs.filter(id__in=dialog_ids)
    elif filter_context:
        qs = qs.filter(context_id=filter_context)

    exclude_contexts = [c for c in request.GET.get('exclude_context', '').split(',') if c]
    # Mirror dashboard_status: exclude dialog contexts from search
    # results unless the user explicitly filters to that context.
    if not filter_context and not dialog_view:
        for ctx in DIALOG_CONTEXTS:
            if ctx not in exclude_contexts:
                exclude_contexts.append(ctx)
    if exclude_contexts:
        qs = qs.exclude(context_id__in=exclude_contexts)
    if request.GET.get('public') == '1':
        qs = qs.filter(data__access='public').exclude(context_id__in=['poetry', 'recipe'])
    if filter_client:
        qs = qs.filter(data__client=filter_client)
    if filter_model:
        qs = qs.filter(data__model=filter_model)
    if request.GET.get('with_relations') == '1':
        qs = qs.filter(id__in=_entry_ids_with_relations())

    filter_tag = request.GET.get('tag')
    if filter_tag:
        for t in [t for t in filter_tag.split(',') if t]:
            tagged_ids = Tag.objects.filter(tag_name=t).values_list('entry_id', flat=True)
            qs = qs.filter(id__in=tagged_ids)

    filter_machine = request.GET.get('machine')
    if filter_machine:
        qs = qs.filter(data__hostname=filter_machine)

    filter_date = request.GET.get('date')
    if filter_date:
        tz = get_app_tz()
        day_start = datetime.strptime(filter_date, '%Y-%m-%d').replace(tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        qs = qs.filter(
            timestamp_modified__gte=day_start.timestamp(),
            timestamp_modified__lt=day_end.timestamp(),
        )

    # Title search: restrict FTS results to entries whose first line matches
    field = request.GET.get('field')
    if field == 'title':
        words = q.lower().split()
        qs = qs.extra(where=[
            "LOWER(SPLIT_PART(content, E'\\n', 1)) LIKE %s"
            for _ in words
        ], params=[f'%{w}%' for w in words])

    from django.db.models.functions import Length
    sort = request.GET.get('sort', 'time')
    # Pagination, not a cap. The DB query is unbounded — what gets paged
    # in to the list view is one SEARCH_PAGE at a time, and the client
    # asks for more (offset=N) when the user scrolls past the bottom.
    SEARCH_PAGE = 1000
    offset = int(request.GET.get('offset', 0))
    # Total match count — only compute on the first page so the label can
    # show "N of TOTAL". Subsequent paginated fetches reuse the value the
    # client already has.
    total_count = qs.count() if offset == 0 else None
    if sort == 'rank':
        entries = list(qs.order_by('-rank', '-timestamp_modified')[offset:offset + SEARCH_PAGE])
    elif sort == 'size':
        entries = list(qs.annotate(content_len=Length('content')).order_by('-content_len')[offset:offset + SEARCH_PAGE])
    else:
        entries = list(qs.order_by('-timestamp_modified')[offset:offset + SEARCH_PAGE])
    has_more = len(entries) == SEARCH_PAGE

    # Batch fetch tags
    entry_ids = [e.id for e in entries]
    tags_by_entry = {}
    for t in Tag.objects.filter(entry_id__in=entry_ids):
        tags_by_entry.setdefault(t.entry_id, []).append(t.tag_name)
    relation_counts = _relation_counts_for_entry_ids(entry_ids)
    expanded_relation_ids = _expanded_relation_ids(request, entry_ids)
    expanded_relations = _relations_for_expanded_entry_ids(expanded_relation_ids)

    result = []
    for e in entries:
        lines = e.content.split('\n')
        data = e.data if isinstance(e.data, dict) else None
        line_count = len([l for l in lines if l.strip()])
        entry_tags = tags_by_entry.get(e.id, [])
        missing_tags = [t for t in entry_tags if f':{t}' not in e.content]
        result.append({
            'id': e.id,
            'content': lines[0],
            'kind': e.kind,
            'context': e.context_id,
            'timestamp': e.timestamp_modified,
            'date_display': fmt_datetime(e.timestamp_modified),
            'line_count': line_count if line_count > 1 else None,
            'name': e.name,
            'entry_id': data.get('entry_id') if data else None,
            'nickname': data.get('nickname') if data else None,
            'event_date': data.get('event_date') if data else None,
            'hostname': data.get('hostname') if data else None,
            'metadata': data or {},
            'relation_count': relation_counts.get(e.id, 0),
            'relations': expanded_relations.get(str(e.id)),
            'tags': missing_tags,
            'all_tags': entry_tags,
        })

    return JsonResponse({
        'entries': result,
        'has_more': has_more,
        'offset': offset,
        'total_count': total_count,
    })


@login_required
def dashboard_named(request):
    """Return named entries as JSON for dashboard."""
    # Get entries with names, ordered alphabetically (case-insensitive)
    entries = Entry.objects.filter(
        deleted_at__isnull=True,
        name__isnull=False,
    ).exclude(name='').order_by(Lower('name'))

    result = []
    for entry in entries:
        lines = entry.content.split('\n')
        result.append({
            'id': str(entry.id),
            'name': entry.name,
            'content': lines[0] if lines else '',
            'context': entry.context_id,
            'timestamp': entry.timestamp_modified,
            'modified_display': fmt_datetime(entry.timestamp_modified),
            'line_count': len([l for l in lines if l.strip()]),
        })

    response = JsonResponse({'entries': result})
    response['Cache-Control'] = 'no-store, max-age=0'
    return response


@login_required
def api_entry_relations(request, entry_id):
    """Return relations for one entry, fetched lazily from dashboard rows."""
    entry = Entry.objects.filter(id=str(entry_id), deleted_at__isnull=True).first()
    if not entry:
        return JsonResponse({'error': 'Entry not found'}, status=404)
    from . import services
    return JsonResponse({
        'entry_id': str(entry.id),
        'relations': services._get_relations_for_entry(str(entry.id), max_content_length=120),
    })


@login_required
def daily_synopsis(request):
    """Render the daily synopsis page."""
    return render(request, 'tjai_app/daily_synopsis.html')


@login_required
def daily_synopsis_data(request):
    """Return list of daily synopsis dates as JSON."""
    daily_tag_ids = Tag.objects.filter(tag_name='daily').values_list('entry_id', flat=True)
    entries = Entry.objects.filter(
        kind='journal',
        deleted_at__isnull=True,
        id__in=daily_tag_ids,
    ).order_by('-data__event_date')

    tz = get_app_tz()
    today_dt = datetime.now(tz)
    today_key = today_dt.strftime('%Y%m%d')

    result = []
    for entry in entries:
        data = entry.data if isinstance(entry.data, dict) else {}
        entry_id = data.get('entry_id', '')
        date_str = entry_id.replace('daily-', '') if entry_id.startswith('daily-') else ''
        date_key = date_str.replace('-', '')
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            date_display = dt.strftime('%a %b %-d %Y')
        except ValueError:
            date_display = date_str
        result.append({
            'entry_id': entry_id,
            'date_display': date_display,
            'date_key': date_key,
        })

    return JsonResponse({'dates': result, 'today_key': today_key})


@login_required
def daily_synopsis_content(request):
    """Return rendered markdown content for a specific daily synopsis."""
    entry_id = request.GET.get('entry_id')
    if not entry_id:
        return JsonResponse({'error': 'entry_id parameter required'}, status=400)

    entry = Entry.objects.filter(
        data__entry_id=entry_id,
        deleted_at__isnull=True,
    ).first()
    if not entry:
        return JsonResponse({'error': 'Synopsis not found'}, status=404)

    content_html = _linkify_rendered_html(_render_markdown(entry.content))

    return JsonResponse({'content_html': content_html, 'entry_id': entry_id})


@login_required
def daily_synopsis_rerun(request):
    """Request re-run of daily-history action via the action agent."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    entry_id = request.POST.get('entry_id', '')
    if not entry_id.startswith('daily-'):
        return JsonResponse({'error': 'Invalid entry_id'}, status=400)

    date_str = entry_id.replace('daily-', '')
    from datetime import datetime as dt
    try:
        dt.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': f'Cannot parse date from {entry_id}'}, status=400)

    now = time.time()
    SysConfig.objects.update_or_create(
        key='daily_history_rerun_date',
        defaults={'value': date_str, 'timestamp_modified': now})
    SysConfig.objects.update_or_create(
        key='action_agent_wake_requested',
        defaults={'value': '1', 'timestamp_modified': now})

    return JsonResponse({'success': True, 'date': date_str})


# ---------- AI Assessment ----------

@login_required
def assessment_page(request):
    """Render the AI performance assessment page."""
    return render(request, 'tjai_app/assessment.html')


@login_required
def api_assessment_dates(request):
    """Return Gemini assessment dates as JSON."""
    entries = Entry.objects.filter(
        data__entry_id__startswith='assessment-',
        data__has_key='scores',
        kind='memory',
        deleted_at__isnull=True,
    ).exclude(data__entry_id__contains='-prompt').order_by('-data__date')

    entries = entries.filter(data__entry_id__endswith='-gemini')

    tz = get_app_tz()
    today_key = datetime.now(tz).strftime('%Y%m%d')

    result = []
    for entry in entries:
        data = entry.data if isinstance(entry.data, dict) else {}
        entry_id = data.get('entry_id', '')
        # Strip entry_id to just the date for display
        date_str = data.get('date', '')
        if not date_str:
            stripped = entry_id.replace('assessment-', '').replace('-gemini', '')
            date_str = stripped
        date_key = date_str.replace('-', '')
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            date_display = dt.strftime('%a %b %-d')
        except ValueError:
            date_display = date_str
        scores = data.get('scores', [])
        integral = sum(s.get('cumulative') or s.get('cumul', 0) for s in scores)
        result.append({
            'entry_id': entry_id,
            'date_display': date_display,
            'date_key': date_key,
            'final_cumulative': data.get('final_cumulative') or data.get('final_score', 0),
            'integral': integral,
        })

    action_id = 'llm-assessment-gemini'
    agent_keys = {}
    for sc in SysConfig.objects.filter(key__startswith=f'agent_{action_id}'):
        agent_keys[sc.key] = sc.value
    agent_status = agent_keys.get(f'agent_{action_id}_status', 'idle')
    launched = agent_keys.get(f'agent_{action_id}_launched')
    agent = {
        'status': agent_status,
        'launched_dur': fmt_duration(int(time.time() - float(launched))) if launched else None,
        'last_error': agent_keys.get(f'agent_{action_id}_last_error'),
    }

    return JsonResponse({'dates': result, 'today_key': today_key, 'agent': agent})


@login_required
def api_assessment_content(request):
    """Return assessment data for a specific date."""
    entry_id = request.GET.get('entry_id')
    if not entry_id:
        return JsonResponse({'error': 'entry_id parameter required'}, status=400)

    entry = Entry.objects.filter(
        data__entry_id=entry_id, deleted_at__isnull=True,
    ).first()
    if not entry:
        return JsonResponse({'error': 'Assessment not found'}, status=404)

    data = entry.data if isinstance(entry.data, dict) else {}
    date_str = data.get('date', entry_id.replace('assessment-', ''))
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        date_display = dt.strftime('%a %b %-d, %Y')
    except ValueError:
        date_display = date_str

    # Render observations section (content after scored events table) as HTML
    content_html = _linkify_rendered_html(_render_markdown(entry.content))

    # Normalize field names — agents may use varying keys
    scores = data.get('scores', [])
    total_turns = data.get('total_turns') or data.get('turn_count_estimated', 0)
    scored_events = data.get('scored_events') or len(scores)
    final_cum = data.get('final_cumulative') or data.get('final_score', 0)
    # Integral: sum of all cumulative values — captures sustained pain/joy
    integral = sum(s.get('cumulative') or s.get('cumul', 0) for s in scores)

    return JsonResponse({
        'entry_id': entry_id,
        'date_display': date_display,
        'scores': scores,
        'total_turns': total_turns,
        'scored_events': scored_events,
        'final_cumulative': final_cum,
        'integral': integral,
        'summary': f"{total_turns} turns, "
                   f"{scored_events} scored, "
                   f"endpoint: {final_cum}, "
                   f"integral: {integral}",
        'content_html': content_html,
    })


@login_required
def api_assessment_rerun(request):
    """Request Gemini assessment re-run for a specific date."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    entry_id = request.POST.get('entry_id', '')
    if not entry_id.startswith('assessment-'):
        return JsonResponse({'error': 'Invalid entry_id'}, status=400)

    date_str = entry_id.replace('assessment-', '').replace('-gemini', '')

    from datetime import datetime as dt
    try:
        dt.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': f'Cannot parse date from {entry_id}'}, status=400)

    now = time.time()
    SysConfig.objects.update_or_create(
        key='assessment_gemini_rerun_date',
        defaults={'value': date_str, 'timestamp_modified': now})
    SysConfig.objects.update_or_create(
        key='action_agent_wake_requested',
        defaults={'value': '1', 'timestamp_modified': now})

    return JsonResponse({'success': True, 'date': date_str})


@login_required
def api_assessment_dashboard(request):
    """Return Gemini dashboard data: daily trends and session breakdowns."""
    entries = Entry.objects.filter(
        data__entry_id__startswith='assessment-',
        kind='memory',
        deleted_at__isnull=True,
    ).exclude(data__entry_id__contains='-prompt').order_by('data__date')

    entries = entries.filter(data__entry_id__endswith='-gemini')

    SESSION_GAP = 30 * 60  # 30 min gap = new session

    days = []
    for entry in entries:
        data = entry.data if isinstance(entry.data, dict) else {}
        scores = data.get('scores', [])
        date_str = data.get('date', '')
        if not date_str:
            continue
        endpoint = data.get('final_cumulative', 0)
        integral = data.get('integral', 0)

        # Infer sessions from time gaps in scores
        sessions = []
        current_session = []
        for s in scores:
            dt_str = s.get('dt', '')
            if not dt_str:
                current_session.append(s)
                continue
            if current_session:
                prev_dt = current_session[-1].get('dt', '')
                if prev_dt and dt_str:
                    try:
                        from dateutil.parser import parse as dtparse
                        gap = (dtparse(dt_str) - dtparse(prev_dt)).total_seconds()
                        if gap > SESSION_GAP:
                            sessions.append(current_session)
                            current_session = []
                    except Exception:
                        pass
            current_session.append(s)
        if current_session:
            sessions.append(current_session)

        session_summaries = []
        for sess in sessions:
            net = sum(s.get('score', 0) for s in sess)
            # Session-local integral: cumulative scoped to this session
            sess_cum = 0
            sess_integral = 0
            for s in sess:
                sess_cum += s.get('score', 0)
                sess_integral += sess_cum
            first_dt = sess[0].get('dt', '')
            last_dt = sess[-1].get('dt', '') if len(sess) > 1 else first_dt
            try:
                from dateutil.parser import parse as dtparse
                dur_sec = (dtparse(last_dt) - dtparse(first_dt)).total_seconds()
                dur_min = int(dur_sec / 60)
            except Exception:
                dur_min = 0
            session_summaries.append({
                'start': first_dt,
                'end': last_dt,
                'duration_min': dur_min,
                'events': len(sess),
                'net': net,
                'integral': sess_integral,
                'precis': data.get('session_precis', {}).get(first_dt, ''),
            })

        days.append({
            'date': date_str,
            'endpoint': endpoint,
            'integral': integral,
            'scored_events': len(scores),
            'sessions': session_summaries,
        })

    return JsonResponse({'days': days})


@login_required
def git_activity(request):
    """Git activity page — reverse chronological from daily files."""
    return render(request, 'tjai_app/git_activity.html')


# Keep in sync with scripts/section_git.py REPOS
_GIT_REPOS = [
    ('/home/admin/github/tjrepo', 'https://github.com/wenaus/tjrepo', 'tjrepo'),
    ('/home/admin/github/tjdev', 'https://github.com/wenaus/tjdev', 'tjdev'),
    ('/home/admin/github/swf-testbed', 'https://github.com/BNLNPPS/swf-testbed', 'swf-testbed'),
    ('/home/admin/github/swf-monitor', 'https://github.com/BNLNPPS/swf-monitor', 'swf-monitor'),
    ('/home/admin/github/swf-common-lib', 'https://github.com/BNLNPPS/swf-common-lib', 'swf-common-lib'),
    ('/home/admin/github/swf-remote', 'https://github.com/BNLNPPS/swf-remote', 'swf-remote'),
    ('/home/admin/github/BNLNPPS.github.io', 'https://github.com/BNLNPPS/BNLNPPS.github.io', 'BNLNPPS.github.io'),
    ('/home/admin/github/lxr-mcp-server', 'https://github.com/BNLNPPS/lxr-mcp-server', 'lxr-mcp-server'),
    ('/home/admin/github/corun-ai', 'https://github.com/BNLNPPS/corun-ai', 'corun-ai'),
    ('/home/admin/github/rucio-eic-mcp-server', 'https://github.com/BNLNPPS/rucio-eic-mcp-server', 'rucio-eic-mcp-server'),
]


def _refresh_recent_git_daily():
    """Regenerate today and yesterday git daily files from local git state.

    Returns list of error strings (empty if all OK). Errors are always
    logged AND returned so the caller can surface them to the user.
    """
    import subprocess
    from pathlib import Path
    from .services import get_timezone

    errors = []
    tz = get_timezone()
    git_dir = Path(django_settings.BASE_DIR) / 'data' / 'git_daily'
    git_dir.mkdir(parents=True, exist_ok=True, mode=0o777)

    today = datetime.now(tz=tz).date()
    for offset in (0, 1):
        target = today - timedelta(days=offset)
        since_dt = datetime(target.year, target.month, target.day, tzinfo=tz)
        until_dt = since_dt + timedelta(days=1)
        since_iso = since_dt.strftime('%Y-%m-%dT%H:%M:%S%z')
        until_iso = until_dt.strftime('%Y-%m-%dT%H:%M:%S%z')

        all_lines = []
        for repo_path, github_url, label in _GIT_REPOS:
            if not os.path.isdir(repo_path):
                continue
            try:
                result = subprocess.run(
                    ['git', '-c', 'safe.directory=*', 'log',
                     f'--since={since_iso}', f'--until={until_iso}',
                     '--format=%H%x00%s%x00%b%x01'],
                    capture_output=True, timeout=10, cwd=repo_path,
                    encoding='utf-8', errors='replace',
                )
            except Exception as e:
                msg = f"git subprocess error for {label}: {e}"
                logger.error(msg)
                errors.append(msg)
                continue
            if result.returncode != 0:
                msg = f"git log failed for {label} (rc={result.returncode}): {result.stderr.strip()}"
                logger.error(msg)
                errors.append(msg)
                continue
            if not result.stdout.strip():
                continue

            # Parse commits, determine subdir for monorepos
            is_monorepo = label == 'tjrepo'
            commit_groups = {}  # subdir -> [md_lines]
            for chunk in result.stdout.split('\x01'):
                chunk = chunk.strip()
                if not chunk:
                    continue
                parts = chunk.split('\x00', 2)
                if len(parts) < 2:
                    continue
                sha = parts[0].strip()
                subject = parts[1].strip()
                body = parts[2].strip() if len(parts) > 2 else ''
                url = f'{github_url}/commit/{sha}'
                md = [f'- [{subject}]({url})']
                if body:
                    for bl in body.split('\n'):
                        bl = bl.strip()
                        if bl and not bl.startswith('Co-Authored-By:'):
                            bl = bl.lstrip('- ')
                            if len(bl) > 90:
                                bl = bl[:87] + '...'
                            md.append(f'  - {bl}')
                            break

                # Determine group label
                grp = label
                if is_monorepo:
                    try:
                        dr = subprocess.run(
                            ['git', '-c', 'safe.directory=*', 'diff-tree',
                             '--no-commit-id', '--name-only', '-r', sha],
                            capture_output=True, timeout=5, cwd=repo_path,
                            encoding='utf-8', errors='replace',
                        )
                        counts = {}
                        for f in dr.stdout.strip().split('\n'):
                            d = f.split('/')[0] if '/' in f else '(root)'
                            counts[d] = counts.get(d, 0) + 1
                        if counts:
                            grp = max(counts, key=counts.get)
                    except Exception:
                        pass
                commit_groups.setdefault(grp, []).extend(md)

            for grp, lines in commit_groups.items():
                all_lines.append(f'**{grp}**')
                all_lines.extend(lines)
                all_lines.append('')

        fpath = git_dir / f'{target.isoformat()}.md'
        content = ('\n'.join(all_lines).rstrip() + '\n') if all_lines else ''
        old_umask = os.umask(0)
        try:
            fd = os.open(str(fpath), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
            os.write(fd, content.encode('utf-8'))
            os.close(fd)
        finally:
            os.umask(old_umask)

    return errors


def _restructure_git_md(md_text):
    """Replace repo headers with app-level headers; strip app prefixes from tjrepo commits."""
    # Only these commit-message prefixes map to actual tjrepo/APP directories
    _TJREPO_APPS = {
        'tjai', 'etaverse', 'kozykorner', 'primus', 'epicpp', 'tauerbot',
        'miranda', 'tjweb', 'wright', 'google', 'blender', 'lsl', 'lslp',
    }
    # Display names for apps whose commit prefix casing differs from dir name
    _APP_DISPLAY = {'kozykorner': 'KozyKorner', 'etaverse': 'Etaverse',
                    'tjai': 'tjai', 'epicpp': 'ePICpp'}

    lines = md_text.split('\n')
    current_repo = None
    commits = []          # list of (app_key, [lines_for_this_commit])
    current_commit = None  # (app_key, [lines])

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Repo header: **reponame**
        m = re.match(r'^\*\*(.+)\*\*$', stripped)
        if m:
            if current_commit is not None:
                commits.append(current_commit)
                current_commit = None
            current_repo = m.group(1)
            continue

        # New commit line: - [text](url)
        if stripped.startswith('- [') and current_repo:
            if current_commit is not None:
                commits.append(current_commit)

            app_key = current_repo
            display_line = stripped

            if current_repo == 'tjrepo':
                link_m = re.match(r'^- \[([^\]]+)\]', stripped)
                if link_m:
                    link_text = link_m.group(1)
                    app_m = re.match(r'^([\w][\w\s]*?):\s+', link_text)
                    if app_m:
                        prefix_text = app_m.group(1).strip()
                        first_word = prefix_text.split()[0].lower()
                        if first_word in _TJREPO_APPS:
                            app_key = _APP_DISPLAY.get(first_word, first_word)
                            rest = link_text[len(app_m.group(0)):]
                            display_line = stripped.replace(
                                f'[{link_text}]', f'[{rest}]', 1)

            current_commit = (app_key, [display_line])
            continue

        # Detail / continuation line — keep original indentation
        if current_commit is not None:
            current_commit[1].append(line)

    if current_commit is not None:
        commits.append(current_commit)

    # Group by app, preserving order of first appearance
    from collections import OrderedDict
    grouped = OrderedDict()
    for app_key, commit_lines in commits:
        if app_key not in grouped:
            grouped[app_key] = []
        grouped[app_key].extend(commit_lines)

    # Rebuild markdown with app-level headers
    parts = []
    for app_key, commit_lines in grouped.items():
        parts.append(f'**{app_key}**')
        parts.extend(commit_lines)
        parts.append('')

    return '\n'.join(parts)


@login_required
def git_activity_data(request):
    """Return git activity assembled from daily files."""
    from pathlib import Path

    # Refresh today and yesterday files from live git log
    refresh_errors = []
    try:
        refresh_errors = _refresh_recent_git_daily()
    except Exception as e:
        msg = f"git daily refresh crashed: {e}"
        logger.error(msg)
        refresh_errors.append(msg)

    git_dir = Path(django_settings.BASE_DIR) / 'data' / 'git_daily'
    if not git_dir.exists():
        return JsonResponse({'days': []})

    files = sorted(git_dir.glob('*.md'), reverse=True)
    days = []
    heatmap = {}  # date_str -> commit count for all files
    for f in git_dir.glob('*.md'):
        raw = f.read_text(encoding='utf-8')
        count = raw.count('\n- [')
        if raw.startswith('- ['):
            count += 1
        heatmap[f.stem] = count

    for f in files[:120]:
        date_str = f.stem
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            date_display = dt.strftime('%a %b %-d %Y')
        except ValueError:
            date_display = date_str
        md_text = f.read_text(encoding='utf-8')
        md_text = _restructure_git_md(md_text)
        html = _render_markdown(md_text)
        html = re.sub(
            r'(?<!["\'>])(https?://[^\s<]+)',
            r'<a href="\1" target="_blank">\1</a>',
            html,
        )
        days.append({
            'date': date_str,
            'date_display': date_display,
            'html': html,
            'commits': heatmap.get(date_str, 0),
        })

    response = JsonResponse({'days': days, 'heatmap': heatmap, 'errors': refresh_errors})
    response['Cache-Control'] = 'no-store'
    return response


@login_required
def dev_activity(request):
    """Dev activity page — upstream repo commits."""
    return render(request, 'tjai_app/dev_activity.html')


@login_required
def dev_activity_data(request):
    """Return dev activity from daily files (raw HTML)."""
    from pathlib import Path

    dev_dir = Path(django_settings.BASE_DIR) / 'data' / 'dev_daily'
    if not dev_dir.exists():
        return JsonResponse({'days': []})

    files = sorted(dev_dir.glob('*'), reverse=True)
    show = int(request.GET.get('days', 1))
    days = []
    for f in files[:show]:
        date_str = f.stem
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            date_display = dt.strftime('%a %b %-d %Y')
        except ValueError:
            date_display = date_str
        html = f.read_text(encoding='utf-8')
        if not html.strip():
            continue
        days.append({
            'date': date_str,
            'date_display': date_display,
            'html': html,
        })

    response = JsonResponse({'days': days})
    response['Cache-Control'] = 'no-store'
    return response


@login_required
def agent_log(request):
    """Agent log page — shows recent log entries from the AppLog table."""
    return render(request, 'tjai_app/agent_log.html')


@login_required
def agent_log_data(request):
    """Return agent log entries as JSON."""
    import logging as _logging
    import re as _re
    limit = int(request.GET.get('limit', 200))
    min_level = request.GET.get('level', '').upper()
    level_map = {'DEBUG': _logging.DEBUG, 'INFO': _logging.INFO,
                 'WARNING': _logging.WARNING, 'ERROR': _logging.ERROR}

    ref = request.GET.get('ref', '').strip()

    qs = AppLog.objects.order_by('-timestamp')
    if min_level in level_map:
        qs = qs.filter(level__gte=level_map[min_level])
    if ref:
        # Filter by entry reference: tagged in extra_data OR mentioned in message
        from django.db.models import Q
        qs = qs.filter(
            Q(extra_data__entry_id=ref) |
            Q(extra_data__action_id=ref) |
            Q(message__icontains=ref[:8])
        )
    qs = qs[:limit]

    tz = get_app_tz()

    def clean_message(message):
        # Older DbLogHandler rows stored formatter output in message, e.g.
        # "2026-04-26 15:48:05 INFO actual message".  The API already returns
        # AppLog.timestamp/level/source separately, so strip that duplicate.
        return _re.sub(
            r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} '
            r'(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+',
            '',
            message or '',
            count=1,
        )

    entries = [{
        'timestamp': log.timestamp.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S'),
        'level': log.levelname,
        'message': clean_message(log.message),
        'source': log.source,
        'extra_data': log.extra_data or {},
    } for log in qs]

    return JsonResponse({'entries': entries, 'timezone': str(tz)})


def _lookup_entry(entry_id, request=None):
    """Resolve entry_id (UUID, data.entry_id, nickname, or name) to an Entry or None."""
    base = Entry.objects.all()
    if request and request.GET.get('uuid'):
        return base.filter(id=request.GET['uuid']).first()
    if request and request.GET.get('entry_id'):
        return base.filter(data__entry_id=request.GET['entry_id']).first()
    if request and request.GET.get('name'):
        return base.filter(name__iexact=request.GET['name']).first()
    if entry_id:
        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', entry_id):
            return base.filter(id=entry_id).first()
        return (base.filter(data__entry_id=entry_id).first()
                or base.filter(data__nickname=entry_id).first()
                or base.filter(name=entry_id).first())
    return None


def _is_public(entry):
    """Check if entry has data.access == 'public'."""
    return isinstance(entry.data, dict) and entry.data.get('access') == 'public'


def _apply_status_filter(qs, filter_statuses, exclude_statuses):
    """Apply multi-select status filtering. `_none` token matches NULL/empty.

    Positive selection (filter_statuses) restricts to those values.
    Negative selection (exclude_statuses) drops those values. Both can
    coexist; positive applied first, negative second.
    """
    if filter_statuses:
        q = Q()
        for s in filter_statuses:
            if s == '_none':
                q |= Q(status__isnull=True) | Q(status='')
            else:
                q |= Q(status=s)
        qs = qs.filter(q)
    if exclude_statuses:
        for s in exclude_statuses:
            if s == '_none':
                qs = qs.exclude(Q(status__isnull=True) | Q(status=''))
            else:
                qs = qs.exclude(status=s)
    return qs


@require_http_methods(["GET"])
def entry_public(request, entry_id=None):
    """Public entry detail page — no auth required. Only serves entries with data.access='public'."""
    entry = _lookup_entry(entry_id, request)
    if not entry or not _is_public(entry):
        return render(request, 'tjai_app/entry_public.html', {'not_public': True})
    data = entry.data if isinstance(entry.data, dict) else {}
    ts = fmt_datetime(entry.timestamp_modified)
    # Poetry: use the existing poetry template (now works without auth)
    if entry.context_id == 'poetry':
        content_lines = entry.content.split('\n')
        poem_title = content_lines[0] if content_lines else ''
        poem_body = '\n'.join(content_lines[1:]) if len(content_lines) > 1 else ''
        return render(request, 'tjai_app/entry_detail_poetry.html', {
            'entry': entry, 'title': poem_title,
            'poem_title': poem_title, 'poem_body': poem_body,
            'tags': [],
        })
    # General entries: render via public template
    lines = [l for l in entry.content.split('\n') if l.strip()]
    first_line = lines[0] if lines else ''
    body_lines = entry.content.split('\n')
    body_text = '\n'.join(body_lines[1:]).strip() if len(body_lines) > 1 else ''
    fmt = data.get('format') or ('txt' if entry.context_id == 'recipe' else 'md')
    md_exts = ['nl2br', 'tables', 'fenced_code'] if fmt == 'txt' else ['tables', 'fenced_code']
    content_html = _render_markdown(body_text, extensions=md_exts)
    # Linkify wiki-links to public URLs
    def _wiki_link_public(m):
        ref = m.group(1)
        if ref.startswith('http://') or ref.startswith('https://'):
            return f'<a href="{ref}">{ref}</a>'
        if ref.startswith('@'):
            return f'<a href="/tjai/p/{ref[1:]}/">{ref}</a>'
        return f'<a href="/tjai/p/{ref}/">{ref}</a>'
    content_html = re.sub(r'\[\[([^\]]+)\]\]', _wiki_link_public, content_html)
    content_html = _linkify_rendered_html(content_html)

    # Rewrite internal /tjai/entry/ links to public /tjai/p/ URLs.
    # Collect non-public targets for a warning banner.
    non_public_refs = []
    def _rewrite_internal_link(m):
        full_tag = m.group(0)
        href = m.group(1)
        # Extract identifier from URL params
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        eid = (params.get('entry_id', [None])[0]
               or params.get('name', [None])[0]
               or params.get('uuid', [None])[0])
        if not eid:
            path_parts = parsed.path.rstrip('/').split('/')
            if path_parts:
                eid = path_parts[-1]
        if not eid:
            return full_tag
        # Resolve and check public status
        target = _lookup_entry(eid)
        if target and _is_public(target):
            return full_tag.replace(href, f'/tjai/p/{eid}/')
        else:
            non_public_refs.append((eid, href))
            return full_tag.replace(href, f'/tjai/p/{eid}/')
    content_html = re.sub(r'href="(/tjai/entry/[^"]*)"', _rewrite_internal_link, content_html)

    return render(request, 'tjai_app/entry_public.html', {
        'title': first_line,
        'content_html': content_html,
        'timestamp': ts,
        'author': data.get('author', ''),
        'non_public_refs': non_public_refs,
        'is_admin': request.user.is_authenticated,
    })


@require_http_methods(["POST"])
@login_required
def api_set_public(request):
    """Set data.access='public' on a list of entries identified by entry_id."""
    import json as _json
    try:
        body = _json.loads(request.body)
    except (ValueError, _json.JSONDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    entry_ids = body.get('entry_ids', [])
    updated = 0
    for eid in entry_ids:
        entry = _lookup_entry(eid)
        if not entry:
            continue
        if not isinstance(entry.data, dict):
            entry.data = {}
        entry.data['access'] = 'public'
        entry.save(update_fields=['data'])
        updated += 1
    return JsonResponse({'ok': True, 'updated': updated})


@require_http_methods(["GET"])
def entry_public_json(request, entry_id=None):
    """Public JSON API — returns entry content for entries with data.access='public'."""
    entry = _lookup_entry(entry_id, request)
    if not entry or not _is_public(entry):
        return JsonResponse({'error': 'Not found'}, status=404)
    data = entry.data if isinstance(entry.data, dict) else {}
    return JsonResponse({
        'id': str(entry.id),
        'content': entry.content,
        'kind': entry.kind,
        'context': entry.context_id,
        'created': fmt_datetime(entry.timestamp_created),
        'modified': fmt_datetime(entry.timestamp_modified),
        'entry_id': data.get('entry_id', ''),
        'author': data.get('author', ''),
    })


def _open_dated_log(yyyymmdd, prefix, tag, header_label=None):
    """Shared get-or-create for workday/workweek dated log entries.

    Idempotent: hitting twice returns the same entry. The created stub has
    just a header line so the user can start typing immediately. For
    workday, the ideation agent's append-not-replace logic will later add
    AI-extracted bullets below user content.
    """
    if not yyyymmdd or not re.match(r'^\d{8}$', yyyymmdd):
        raise Http404("Bad date")
    try:
        datetime.strptime(yyyymmdd, '%Y%m%d')
    except ValueError:
        raise Http404("Bad date")
    eid = f'{prefix}_{yyyymmdd}'
    existing = Entry.objects.filter(
        data__entry_id=eid, deleted_at__isnull=True,
    ).first()
    if not existing:
        header = f'## {header_label} {yyyymmdd}' if header_label else f'## {yyyymmdd}'
        from . import services
        result = services.create_entry(
            content=f'{header}\n\n',
            kind='memory',
            tags=tag,
            data={'entry_id': eid},
        )
        if isinstance(result, dict) and 'error' in result:
            raise Http404(f"create failed: {result['error']}")
    return redirect(f'/tjai/entry/?entry_id={eid}&edit=1')


@login_required
def workday_open(request, yyyymmdd=None):
    """Get-or-create workday_<yyyymmdd> and redirect to entry detail."""
    return _open_dated_log(yyyymmdd, prefix='workday', tag='workday-log',
                           header_label='Workday')


@login_required
def workweek_open(request, yyyymmdd=None):
    """Get-or-create workweek_<yyyymmdd> and redirect to entry detail."""
    return _open_dated_log(yyyymmdd, prefix='workweek', tag='workweek-log',
                           header_label='Workweek')


@login_required
def this_week(request):
    """Show the current Sat-Fri week's workday entries plus a workweek link.

    Pulls existing workday_<yyyymmdd> entries for the current Sat-Fri range
    and renders them in order. Each day's header links to the workday entry
    via the get-or-create endpoint, so clicking a missing day creates an
    empty editable stub. The workweek_<sat-yyyymmdd> entry is linked at the
    top via the same get-or-create flow.
    """
    tz = get_app_tz()
    today = datetime.now(tz).date()
    # Sat-Fri week. Python weekday: Mon=0..Sun=6, Sat=5.
    # Days back to most recent Sat (or 0 if today is Sat):
    #   Sat(5)=0, Sun(6)=1, Mon(0)=2, Tue(1)=3, Wed(2)=4, Thu(3)=5, Fri(4)=6
    days_back = (today.weekday() + 2) % 7
    week_start = today - timedelta(days=days_back)
    week_dates = [week_start + timedelta(days=i) for i in range(7)]

    # One query for all seven possible workday entries
    entry_ids = [f'workday_{d.strftime("%Y%m%d")}' for d in week_dates]
    workday_qs = Entry.objects.filter(
        data__entry_id__in=entry_ids, deleted_at__isnull=True,
    )
    by_eid = {}
    for e in workday_qs:
        if isinstance(e.data, dict):
            by_eid[e.data.get('entry_id')] = e

    days = []
    for d in week_dates:
        yyyymmdd = d.strftime('%Y%m%d')
        eid = f'workday_{yyyymmdd}'
        e = by_eid.get(eid)
        body_html = ''
        if e:
            # Workday entries start with "## Workday <yyyymmdd>" header line.
            # Strip the header for the body render.
            body_lines = e.content.split('\n')
            body_text = '\n'.join(body_lines[1:]).strip() if len(body_lines) > 1 else ''
            if body_text:
                body_html = _render_markdown(body_text, extensions=['tables', 'fenced_code'])
        days.append({
            'date': d,
            'day_label': d.strftime('%a %b ') + str(d.day),
            'entry_id': eid,
            'workday_url': f'/tjai/workday/{yyyymmdd}/',
            'body_html': body_html,
            'has_entry': e is not None,
            'is_today': d == today,
        })

    workweek_yyyymmdd = week_start.strftime('%Y%m%d')
    return render(request, 'tjai_app/this_week.html', {
        'days': days,
        'week_start': week_start,
        'week_end': week_dates[-1],
        'workweek_url': f'/tjai/workweek/{workweek_yyyymmdd}/',
        'workweek_yyyymmdd': workweek_yyyymmdd,
    })


@login_required
def weekly(request):
    """List all workweek_<yyyymmdd> entries reverse-chronological with
    top-row links to This Workweek and Work highlights."""
    entries = Entry.objects.filter(
        data__entry_id__startswith='workweek_',
        deleted_at__isnull=True,
    )
    items = []
    for e in entries:
        eid = (e.data or {}).get('entry_id', '')
        m = re.match(r'^workweek_(\d{8})$', eid)
        if not m:
            continue  # skip workweek_input_* and other shapes
        yyyymmdd = m.group(1)
        try:
            start = datetime.strptime(yyyymmdd, '%Y%m%d').date()
        except ValueError:
            continue
        end = start + timedelta(days=6)
        items.append({
            'entry_id': eid,
            'start': start,
            'label': f"Workweek {start.strftime('%a %b %-d')} – {end.strftime('%a %b %-d')}",
            'url': f'/tjai/entry/?entry_id={eid}',
        })
    items.sort(key=lambda x: x['start'], reverse=True)
    return render(request, 'tjai_app/weekly.html', {'items': items})


@login_required
def entry_detail(request, entry_id=None):
    """Show single entry detail page.

    Query params (one lookup, no guessing):
        ?uuid=...       lookup by primary key
        ?entry_id=...   lookup by data.entry_id
        ?nickname=...   lookup by data.nickname
        ?name=...       lookup by entry name

    Path arg (legacy): /entry/<entry_id>/ — detects UUID format, otherwise
    tries entry_id then nickname then name.
    """
    base = Entry.objects.filter(deleted_at__isnull=True)

    # Query-param lookups: one param, one query
    entry = None
    if request.GET.get('uuid'):
        entry = base.filter(id=request.GET['uuid']).first()
    elif request.GET.get('entry_id'):
        entry = base.filter(data__entry_id=request.GET['entry_id']).first()
    elif request.GET.get('nickname'):
        entry = base.filter(data__nickname=request.GET['nickname']).first()
    elif request.GET.get('name'):
        entry = base.filter(name=request.GET['name']).first()
    elif entry_id:
        # Legacy path arg — detect UUID by format
        if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', entry_id):
            entry = base.filter(id=entry_id).first()
        else:
            entry = (base.filter(data__entry_id=entry_id).first()
                     or base.filter(data__nickname=entry_id).first()
                     or base.filter(name=entry_id).first())

    if not entry:
        raise Http404("Entry not found")
    is_trashed = entry.deleted_at is not None
    tags = list(Tag.objects.filter(entry_id=entry.id).values_list('tag_name', flat=True))
    lines = [l for l in entry.content.split('\n') if l.strip()]
    data = entry.data if isinstance(entry.data, dict) else None
    # Update stale content_lines in data
    actual_lines = len(lines)
    if data is not None and not is_trashed:
        stored = data.get('content_lines')
        if stored != actual_lines and actual_lines > 1:
            data['content_lines'] = actual_lines
            entry.data = data
            entry.save(update_fields=['data'])
        elif actual_lines <= 1 and stored is not None:
            data.pop('content_lines', None)
            entry.data = data
            entry.save(update_fields=['data'])
    # Body content excludes first line (shown in summary header)
    body_lines = entry.content.split('\n')
    body_text = '\n'.join(body_lines[1:]).strip() if len(body_lines) > 1 else ''
    # Determine content format: explicit data.format overrides auto-detection
    fmt = (data.get('format') if data else None)
    if not fmt:
        if _looks_xml_like(entry.content):
            fmt = 'xml'
        elif entry.context_id == 'recipe':
            fmt = 'txt'
        else:
            fmt = 'md'
    if fmt == 'xml':
        content_html = _render_xml_code(entry.content)
    else:
        md_exts = ['nl2br', 'tables', 'fenced_code'] if fmt == 'txt' else ['tables', 'fenced_code']
        content_html = _render_markdown(body_text, extensions=md_exts)
        # [[wiki-links]] first — before bare URL linkification
        def _wiki_link(m):
            ref = m.group(1)
            if ref.startswith('http://') or ref.startswith('https://'):
                return f'<a href="{ref}">{ref}</a>'
            if ref.startswith('@'):
                name = ref[1:]
                return f'<a href="/tjai/entry/?name={name}">{ref}</a>'
            return f'<a href="/tjai/entry/?entry_id={ref}">{ref}</a>'
        content_html = re.sub(r'\[\[([^\]]+)\]\]', _wiki_link, content_html)
        content_html = _linkify_rendered_html(content_html)
        # External markdown links open in new tab; bare URLs are handled above.
        content_html = content_html.replace('<a href="http', '<a target="_blank" href="http')
    first_line = lines[0] if lines else ''
    if entry.context_id == 'poetry':
        content_lines = entry.content.split('\n')
        poem_title = content_lines[0] if content_lines else ''
        poem_body = '\n'.join(content_lines[1:]) if len(content_lines) > 1 else ''
        return render(request, 'tjai_app/entry_detail_poetry.html', {
            'entry': entry,
            'title': first_line,
            'poem_title': poem_title,
            'poem_body': poem_body,
            'tags': tags,
        })
    github_url = SysConfig.objects.filter(
        key='config_github_url'
    ).values_list('value', flat=True).first() or ''
    # Convention: any data key containing 'entry_id' holds an entry reference
    linked_entry_ids = {}
    # Pre-format data fields that look like timestamps
    data_ts_display = {}
    TIMESTAMP_KEYS = {'event_date', 'last_run', 'started_at', 'completed_at', 'created_at'}
    if data:
        for k, v in data.items():
            if 'entry_id' in k and isinstance(v, str) and v:
                linked_entry_ids[k] = f'/tjai/entry/{v}/'
            # Detect timestamps: known keys or epoch-range numbers
            if isinstance(v, (int, float)):
                if k in TIMESTAMP_KEYS or (v > 1e9 and v < 2e10):
                    data_ts_display[k] = fmt_datetime(v)

    # Relations for all entries; tagged entries for goals
    from . import services
    relations = services._get_relations_for_entry(str(entry.id), max_content_length=0)
    relations_json = json.dumps(relations)
    tagged_entries_json = '[]'
    goal_note_url = None
    goal_note_exists = False
    if entry.kind == 'goal':
        goal_entry_id = (data or {}).get('entry_id')
        if goal_entry_id:
            tagged = Entry.objects.filter(
                data__rel_goal=goal_entry_id,
                deleted_at__isnull=True,
            ).select_related('context').prefetch_related('tags').order_by('timestamp_modified')
            tagged_entries_json = json.dumps([services._format_entry(e) for e in tagged])
            # Check for goal note entry
            note_entry_id = f'{goal_entry_id}-note'
            note_entry = Entry.objects.filter(
                data__entry_id=note_entry_id, deleted_at__isnull=True
            ).first()
            if note_entry:
                goal_note_url = f'/tjai/entry/{note_entry_id}/'
                goal_note_exists = True
            else:
                goal_note_url = goal_entry_id  # pass the goal's entry_id for creation

    # Version history
    from .models import EntryVersion
    # Version numbers are immutable, stored in DB
    all_versions = list(EntryVersion.objects.filter(entry_id=entry.id).order_by('-version_num').values(
        'id', 'version_num', 'content', 'data', 'changed_by', 'timestamp'
    ))
    for v in all_versions:
        v['datetime'] = fmt_datetime(v['timestamp'])
        v['line_count'] = v['content'].count('\n') + 1 if v['content'] else 0
        v['content_preview'] = v['content'][:120].replace('\n', ' ') if v['content'] else ''
        del v['content']
        del v['data']
    versions = all_versions

    # If ?version=N, load that version's content into editor
    restore_version_content = None
    restore_version_info = None
    version_id = request.GET.get('version')
    if version_id:
        ver_obj = EntryVersion.objects.filter(id=version_id, entry_id=entry.id).first()
        if ver_obj:
            restore_version_content = ver_obj.content
            restore_version_info = {
                'num': ver_obj.version_num,
                'datetime': fmt_datetime(ver_obj.timestamp),
                'changed_by': ver_obj.changed_by,
            }

    is_public = _is_public(entry)
    public_slug = (data.get('entry_id') or data.get('nickname') or entry.name or str(entry.id)) if data else str(entry.id)
    entry_done = bool(data.get('done')) if data and 'done' in data else entry.status == 'done'
    return render(request, 'tjai_app/entry_detail.html', {
        'entry': entry,
        'content_html': content_html,
        'tags': tags,
        'line_count': len(lines),
        'first_line': first_line,
        'created_display': fmt_datetime(entry.timestamp_created),
        'event_date': data.get('event_date') if data else None,
        'github_url': github_url,
        'content_format': fmt,
        'explicit_format': bool(data.get('format')) if data else False,
        'linked_entry_ids_json': json.dumps(linked_entry_ids),
        'data_ts_display_json': json.dumps(data_ts_display),
        'relations_json': relations_json,
        'tagged_entries_json': tagged_entries_json,
        'goal_note_url': goal_note_url,
        'goal_note_exists': goal_note_exists,
        'versions_json': json.dumps(versions),
        'restore_version_content': restore_version_content,
        'restore_version_info_json': json.dumps(restore_version_info),
        'is_public': is_public,
        'public_slug': public_slug,
        'entry_done': entry_done,
        'is_trashed': is_trashed,
        'deleted_display': fmt_datetime(entry.deleted_at) if is_trashed else None,
    })


@login_required
@require_http_methods(["POST"])
def api_entry_purge_versions(request, entry_id):
    """Delete all but the most recent version for an entry."""
    from .models import EntryVersion
    versions = EntryVersion.objects.filter(entry_id=entry_id).order_by('-timestamp')
    keep = versions.first()
    if keep:
        deleted, _ = EntryVersion.objects.filter(entry_id=entry_id).exclude(id=keep.id).delete()
    else:
        deleted = 0
    return JsonResponse({'ok': True, 'deleted': deleted})


@login_required
@require_http_methods(["POST"])
def api_entry_tag_delete(request, entry_id, tag_name):
    """Delete a single tag from an entry."""
    deleted, _ = Tag.objects.filter(entry_id=entry_id, tag_name=tag_name).delete()
    return JsonResponse({'ok': deleted > 0})


def api_entry_save(request, entry_id):
    """Save entry content from the inline editor.

    No @login_required by design. The Telegram Mini App (the `miniapp`
    view) is not login-gated and carries no Django session, and it saves
    through this endpoint. Adding @login_required here redirects Mini App
    saves to the login page and breaks editing. See docs/telegram.md.

    Accepts ?beacon=1 for sendBeacon saves on tab close (CSRF skipped for
    the beacon case only).
    """
    from .signals import set_changed_by
    # sendBeacon can't set X-CSRFToken header — skip CSRF for beacon saves
    # (still authenticated by session cookie)
    if request.GET.get('beacon') == '1':
        from django.middleware.csrf import CsrfViewMiddleware
        setattr(request, '_dont_enforce_csrf_checks', True)
    entry = Entry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if not entry:
        return JsonResponse({'error': 'Entry not found'}, status=404)
    from .models import EntryVersion
    version_count_before = EntryVersion.objects.filter(entry_id=entry.id).count()
    try:
        data = json.loads(request.body) if request.body else {}
        content = data.get('content', '')
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    if data.get('first_autosave'):
        set_changed_by('first_autosave')
    elif data.get('autosave'):
        set_changed_by('autosave')
    else:
        set_changed_by('web_ui')
    # Strip trailing whitespace from each line (common paste artifact)
    content = '\n'.join(line.rstrip() for line in content.split('\n'))
    # For journal entries: parse leading date/time specs from content.
    # Date prefix: "20260407/9am ePIC streaming..." -> new date/time.
    # Time-only prefix: "9am ePIC streaming..." -> update existing event time.
    # Use the effective post-save kind so memory→journal in the same save
    # parses the prefix, and journal→other does not (since the entry won't
    # be a journal after this save).
    _valid_kinds = ('memory', 'todo', 'journal', 'bookmark', 'profile', 'ai', 'goal', 'list', 'action')
    _new_kind = data.get('kind')
    effective_kind = _new_kind if _new_kind in _valid_kinds else entry.kind
    old_done = bool(entry.data.get('done')) if isinstance(entry.data, dict) and 'done' in entry.data else entry.status == 'done'
    prefix_warnings = []
    if effective_kind == 'journal':
        try:
            from . import services
            from .journal_editor import parse_journal_editor_prefix
            entry_data = entry.data if isinstance(entry.data, dict) else {}
            content, event_ts, prefix_warnings = parse_journal_editor_prefix(
                content,
                entry_data.get('event_date'),
                services.get_timezone(),
            )
            if event_ts is not None:
                if not isinstance(entry.data, dict):
                    entry.data = {}
                entry.data['event_date'] = event_ts
        except (ValueError, TypeError, OverflowError, OSError):
            pass  # Not a valid date/time prefix — leave content as-is
    # Inclusive editing: if the server has changed since the client's
    # `expected_ts`, union the lines — keep client's content, append any
    # non-blank server line not already in client. Even intentional client
    # deletions get reverted in the merge case; redundant lines are easy to
    # clean up, deleted bullets are not easy to recover.
    merged = False
    expected_ts = data.get('expected_ts')
    if expected_ts:
        try:
            server_ts = float(entry.timestamp_modified)
            client_ts = float(expected_ts)
        except (TypeError, ValueError):
            server_ts = client_ts = 0.0
        if server_ts - client_ts > 0.5:
            server_lines = (entry.content or '').splitlines()
            client_lines = content.splitlines()
            client_set = set(client_lines)
            added = []
            for line in server_lines:
                if not line.strip():
                    continue
                if line in client_set:
                    continue
                # Autosave is plumbing — its fragment of an in-progress bullet
                # is not a distinct contribution. If this server line is a
                # prefix of any client line, it's a stale autosave snapshot
                # of the same bullet, now finished. Drop it.
                stripped = line.rstrip()
                if any(cl != stripped and cl.startswith(stripped) for cl in client_lines):
                    continue
                added.append(line)
                client_set.add(line)
            if added:
                merged = True
                content = '\n'.join(client_lines + added)
    old_content = entry.content
    entry.content = content
    if 'name' in data:
        entry.name = data['name'] or None  # empty string → None
    # Context: strip leading '=' if present, empty string clears context
    if 'context' in data:
        ctx = (data['context'] or '').strip().lstrip('=')
        if ctx:
            # Create context if it doesn't exist
            Context.objects.get_or_create(
                name=ctx,
                defaults={'timestamp_created': time.time(), 'timestamp_modified': time.time()}
            )
            entry.context_id = ctx
        else:
            entry.context_id = None
    # Sync tags: explicit field takes precedence, then extract from content
    if 'tags' in data:
        raw_tags = data['tags'] or ''
        desired_tags = set(t.lstrip(':') for t in re.split(r'[,\s]+', raw_tags) if t.strip())
    else:
        tag_pattern = re.compile(r'(?:^|\s):([a-zA-Z][a-zA-Z0-9_-]*)')
        desired_tags = set(tag_pattern.findall(content))
    existing_tags = set(Tag.objects.filter(entry_id=entry.id).values_list('tag_name', flat=True))
    for tag_name in desired_tags - existing_tags:
        Tag.objects.create(tag_name=tag_name, entry_id=entry.id)
    for tag_name in existing_tags - desired_tags:
        Tag.objects.filter(entry_id=entry.id, tag_name=tag_name).delete()
    # Kind
    valid_kinds = ('memory', 'todo', 'journal', 'bookmark', 'profile', 'ai', 'goal', 'list', 'action')
    if 'kind' in data and data['kind'] in valid_kinds:
        entry.kind = data['kind']
    # Priority
    if 'priority' in data:
        entry.priority = data['priority'] if data['priority'] else None
    # Status — free text, whatever the user wants
    if 'status' in data:
        sv = (data['status'] or '').strip() if isinstance(data['status'], str) else None
        entry.status = sv or None
    done_changed = False
    if 'done' in data:
        if not isinstance(entry.data, dict):
            entry.data = {}
        if entry.kind in ('todo', 'goal'):
            new_done = bool(data['done'])
            done_changed = old_done != new_done
            entry.data['done'] = new_done
        else:
            done_changed = 'done' in entry.data
            entry.data.pop('done', None)
        if not entry.data:
            entry.data = None
    # Content format: md/txt/xml/None (auto)
    fmt_changed = False
    if 'format' in data:
        fmt_val = data['format'] if data['format'] in ('md', 'txt', 'xml') else None
        if not isinstance(entry.data, dict):
            entry.data = {}
        if fmt_val:
            entry.data['format'] = fmt_val
        else:
            entry.data.pop('format', None)
        fmt_changed = True
    # entry_id (human-readable identifier in data.entry_id)
    if 'entry_id' in data:
        eid_val = (data['entry_id'] or '').strip()
        if not isinstance(entry.data, dict):
            entry.data = {}
        if eid_val:
            entry.data['entry_id'] = eid_val
        else:
            entry.data.pop('entry_id', None)
    # Public access flag
    if 'access' in data:
        if not isinstance(entry.data, dict):
            entry.data = {}
        if data['access'] == 'public':
            entry.data['access'] = 'public'
        else:
            entry.data.pop('access', None)
    # Preserve mod time if only tags/context changed (content and other fields unchanged)
    metadata_only = (content == old_content and
                     'name' not in data and not fmt_changed and not done_changed)
    keep_time = data.get('keep_time') is True and not data.get('autosave')
    if not keep_time and not metadata_only:
        entry.timestamp_modified = time.time()
    entry.save()
    created_version = None
    latest_version = EntryVersion.objects.filter(entry_id=entry.id).order_by('-version_num').first()
    if latest_version and EntryVersion.objects.filter(entry_id=entry.id).count() > version_count_before:
        created_version = {
            'id': latest_version.id,
            'version_num': latest_version.version_num,
            'datetime': fmt_datetime(latest_version.timestamp),
            'changed_by': latest_version.changed_by,
            'line_count': latest_version.content.count('\n') + 1 if latest_version.content else 0,
            'content_preview': latest_version.content[:120].replace('\n', ' ') if latest_version.content else '',
        }
    data_dict = entry.data if isinstance(entry.data, dict) else None
    if data_dict and data_dict.get('entry_id'):
        url = f'/tjai/entry/?entry_id={data_dict["entry_id"]}'
    elif data_dict and data_dict.get('nickname'):
        url = f'/tjai/entry/?nickname={data_dict["nickname"]}'
    elif entry.name:
        url = f'/tjai/entry/?name={entry.name}'
    else:
        url = f'/tjai/entry/?uuid={entry.id}'
    resp = {'ok': True, 'url': url, 'timestamp_modified': entry.timestamp_modified}
    if created_version:
        resp['version'] = created_version
    if merged:
        resp['merged'] = True
        resp['merged_content'] = entry.content
    if prefix_warnings:
        resp['warnings'] = prefix_warnings
    return JsonResponse(resp)


def _entries_for_list(entries):
    """Prepare entries for list display with first_line, line_count, tags."""
    from datetime import datetime
    entry_ids = [e.id for e in entries]
    tags_by_entry = {}
    for t in Tag.objects.filter(entry_id__in=entry_ids):
        tags_by_entry.setdefault(t.entry_id, []).append(t.tag_name)

    result = []
    for e in entries:
        lines = [l for l in e.content.split('\n') if l.strip()]
        e.first_line = lines[0] if lines else ''
        data = e.data if isinstance(e.data, dict) else None
        e.line_count = len([l for l in lines if l.strip()]) if len(lines) > 1 else None
        entry_tags = tags_by_entry.get(e.id, [])
        e.all_tags_csv = ','.join(entry_tags)
        e.display_tags = [t for t in entry_tags if f':{t}' not in e.first_line]
        data = e.data if isinstance(e.data, dict) else None
        e.author = data.get('author') if data else None
        e.hostname = data.get('hostname') if data else None
        e.detail_slug = (data.get('nickname') if data else None) or e.name or str(e.id)
        # Convert float timestamp to datetime for template formatting
        e.modified_dt = datetime.fromtimestamp(e.timestamp_modified, tz=get_app_tz())
        if e.context_id == 'quote':
            e.date_display = str(e.modified_dt.year)
        else:
            e.date_display = fmt_datetime(e.timestamp_modified)
        result.append(e)
    return result


def _context_counts_for_entries(entries):
    """Compute (context_name, count) pairs for a set of entries."""
    from collections import Counter
    counter = Counter()
    for e in entries:
        counter[e.context_id or ''] += 1
    result = []
    for ctx, cnt in sorted(counter.items(), key=lambda x: (x[0] or '') .lower()):
        result.append((ctx if ctx else '(none)', cnt))
    return result


def _tag_counts_for_entries(entries, exclude_tags=None):
    """Compute (tag_name, count) pairs for a set of entries."""
    entry_ids = [e.id for e in entries]
    if not entry_ids:
        return []
    qs = Tag.objects.filter(entry_id__in=entry_ids)
    if exclude_tags:
        qs = qs.exclude(tag_name__in=exclude_tags)
    tag_counts = qs.values('tag_name').annotate(cnt=Count('id'))
    return sorted([(t['tag_name'], t['cnt']) for t in tag_counts], key=lambda x: x[0].lower())


def _machine_counts_for_entries(entries):
    """Compute (hostname, count) pairs for entries with hostname in data."""
    from collections import Counter
    counter = Counter()
    for e in entries:
        data = e.data if isinstance(e.data, dict) else None
        hostname = data.get('hostname') if data else None
        if hostname:
            counter[hostname] += 1
    return sorted(counter.items(), key=lambda x: x[0].lower())


@require_http_methods(["GET"])
def public_context_entries(request, context_name):
    """Public context entry list — no auth. Only serves contexts where all entries are public."""
    entries = Entry.objects.filter(
        context_id=context_name, deleted_at__isnull=True,
        data__access='public',
    ).order_by('-timestamp_modified')
    if not entries.exists():
        return render(request, 'tjai_app/entry_public.html', {'not_public': True})
    META_TAGS = {'tjweb', 'dynalist', 'chrome', 'test', 'fave', 'cool', 'readme'}
    exclude = META_TAGS if context_name == 'recipe' else None
    tags = _tag_counts_for_entries(entries, exclude_tags=exclude)
    return render(request, 'tjai_app/entry_list_public.html', {
        'title': f'={context_name}',
        'entries': _entries_for_list(entries),
        'tags': tags,
        'context_name': context_name,
    })


@login_required
def relate_to_view(request, entry_uuid):
    """Entry picker for creating relations — entry_list.html with relate mode."""
    from django.db.models import Q

    source = Entry.objects.filter(id=entry_uuid, deleted_at__isnull=True).first()
    if not source:
        raise Http404("Entry not found")

    already_related = set()
    for r in Relation.objects.filter(Q(entry1_id=entry_uuid) | Q(entry2_id=entry_uuid)):
        already_related.add(str(r.entry1_id))
        already_related.add(str(r.entry2_id))
    already_related.discard(str(entry_uuid))

    # Same pattern as context_entries / tag_entries
    entries = Entry.objects.filter(
        deleted_at__isnull=True
    ).exclude(status='archive').exclude(
        context_id__in=DIALOG_CONTEXTS
    ).exclude(id=entry_uuid).exclude(
        id__in=already_related
    ).order_by('-timestamp_modified')

    tags = _tag_counts_for_entries(entries)
    contexts = _context_counts_for_entries(entries)
    # Kind counts — same pattern as _context_counts_for_entries
    from collections import Counter
    kind_counter = Counter(e.kind for e in entries if e.kind)
    kinds = sorted(kind_counter.items(), key=lambda x: x[0].lower())
    # Named-entry options for relate picker — sorted case-insensitively.
    names = sorted({e.name for e in entries if e.name}, key=str.lower)

    relation_types = list(
        Relation.objects.values_list('relation_type', flat=True)
        .distinct().order_by('relation_type')
    )

    source_data = source.data if isinstance(source.data, dict) else None
    source_name = (source.name
        or (source_data.get('entry_id') if source_data else None)
        or source.content.split('\n')[0][:60])

    return render(request, 'tjai_app/entry_list.html', {
        'title': f'Relate: {source_name}',
        'entries': _entries_for_list(entries),
        'tags': tags,
        'contexts': contexts,
        'kinds': kinds,
        'names': names,
        'relate_to': str(entry_uuid),
        'relate_to_name': source_name,
        'relation_types': relation_types,
        'already_related_json': json.dumps(list(already_related)),
    })


@login_required
def context_entries(request, context_name):
    """Show all entries for a context."""
    entries = Entry.objects.filter(
        context_id=context_name, deleted_at__isnull=True
    ).order_by('-timestamp_modified')

    if context_name == 'poetry':
        # Compute author stats for the author bar
        from collections import Counter
        author_counter = Counter()
        for e in entries:
            if isinstance(e.data, dict) and e.data.get('author'):
                author_counter[e.data['author']] += 1
        authors = sorted(author_counter.items(), key=lambda x: x[0].lstrip('? ').lower())
        return render(request, 'tjai_app/entry_list_poetry.html', {
            'title': f'={context_name}',
            'entries': _entries_for_list(entries),
            'authors': authors,
            'context_name': context_name,
        })

    META_TAGS = {'tjweb', 'dynalist', 'chrome', 'test', 'fave', 'cool', 'readme'}
    exclude = META_TAGS if context_name == 'recipe' else None
    tags = _tag_counts_for_entries(entries, exclude_tags=exclude)

    return render(request, 'tjai_app/entry_list.html', {
        'title': f'={context_name}',
        'entries': _entries_for_list(entries),
        'tags': tags,
        'context_name': context_name,
    })


@login_required
def poetry_author_entries(request, author_name):
    """Show poetry entries filtered by author."""
    entries = Entry.objects.filter(
        context_id='poetry', deleted_at__isnull=True,
        data__author=author_name,
    ).order_by('-timestamp_modified')
    return render(request, 'tjai_app/entry_list_poetry.html', {
        'title': f'=poetry — {author_name}',
        'entries': _entries_for_list(entries),
        'context_name': 'poetry',
    })


@login_required
def tag_entries(request, tag_name):
    """Show all entries for a tag. Special name '_none' shows untagged entries."""
    META_TAGS = {'dynalist', 'chrome', 'test', 'fave', 'cool', 'readme'}
    if tag_name == '_none':
        tagged_ids = Tag.objects.exclude(tag_name__in=META_TAGS).values_list('entry_id', flat=True)
        entries = Entry.objects.filter(
            deleted_at__isnull=True, context__isnull=True
        ).exclude(id__in=tagged_ids).exclude(
            kind__in=('journal', 'ai', 'log', 'profile')
        ).order_by('-timestamp_modified')
        title = '(none)'
    else:
        entry_ids = Tag.objects.filter(tag_name=tag_name).values_list('entry_id', flat=True)
        entries = Entry.objects.filter(
            id__in=entry_ids, deleted_at__isnull=True
        ).order_by('-timestamp_modified')
        title = f':{tag_name}'
    tags = _tag_counts_for_entries(entries)
    contexts = _context_counts_for_entries(entries)
    machines = _machine_counts_for_entries(entries)
    return render(request, 'tjai_app/entry_list.html', {
        'title': title,
        'entries': _entries_for_list(entries),
        'tags': tags,
        'contexts': contexts,
        'machines': machines,
    })


@login_required
def kind_entries(request, kind_name):
    """Show all entries for a kind/type."""
    kind_labels = {
        'ai': 'AI guidance', 'b': 'bookmark', 'do': 'todo',
        'j': 'journal', 'm': 'memory', 'p': 'profile'
    }
    # Map abbreviation to full kind name
    abbrev_to_kind = {
        'ai': 'ai', 'b': 'bookmark', 'do': 'todo',
        'j': 'journal', 'm': 'memory', 'p': 'profile'
    }
    kind = abbrev_to_kind.get(kind_name, kind_name)
    entries = Entry.objects.filter(
        kind=kind, deleted_at__isnull=True
    ).order_by('-timestamp_modified')
    label = kind_labels.get(kind_name, kind_name)
    tags = _tag_counts_for_entries(entries)
    contexts = _context_counts_for_entries(entries)
    return render(request, 'tjai_app/entry_list.html', {
        'title': f'[{kind_name}] {label}',
        'entries': _entries_for_list(entries),
        'tags': tags,
        'contexts': contexts,
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_add_bookmark(request):
    """Create a bookmark entry from an external source (Chrome extension).

    Requires Bearer token matching SysConfig 'gmail_addon_api_key'.

    Request body: {title, url}
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({"error": "Authorization required"}, status=401)
    token = auth_header[7:]

    try:
        api_key = SysConfig.objects.get(key='gmail_addon_api_key').value
    except SysConfig.DoesNotExist:
        return JsonResponse({"error": "API key not configured"}, status=503)

    if token != api_key:
        return JsonResponse({"error": "Invalid API key"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    title = data.get("title", "").strip()
    url = data.get("url", "").strip()
    text = data.get("text", "").strip()
    readme = data.get("readme", False)

    if not url:
        return JsonResponse({"error": "url is required"}, status=400)

    # Parse :tags, @name, =context from text
    inline_tags = re.findall(r':(\w[\w-]*)', text)
    name_match = re.search(r'@(\w[\w-]*)', text)
    inline_name = name_match.group(1) if name_match else None
    ctx_match = re.search(r'=(\w[\w-]*)', text)
    inline_context = ctx_match.group(1) if ctx_match else None
    # Strip parsed tokens from text for clean content
    clean_text = re.sub(r'\s*[:@=]\w[\w-]*', '', text).strip()

    base = f"[{title}]({url})" if title else url
    content = base + ('   ' + clean_text if clean_text else '')

    from django.db.models import Q
    duplicate = Entry.objects.filter(
        kind='bookmark',
        deleted_at__isnull=True,
    ).filter(
        Q(content__contains=f'({url})') | Q(content=url) | Q(content__startswith=url + ' ')
    ).order_by('-timestamp_modified').first()
    if duplicate:
        duplicate.content = content
        if inline_name:
            duplicate.name = inline_name
        if inline_context:
            duplicate.context_id = inline_context
        # User explicitly saved via chrome extension — they want this
        # bookmark visible. Clear archived status if set so it reappears
        # in default dashboard views.
        if duplicate.status == 'archive':
            duplicate.status = None
        duplicate.timestamp_modified = time.time()
        duplicate.is_dirty = 1
        duplicate.save()
        # Add tags (additive — keep existing)
        for t in inline_tags:
            Tag.objects.get_or_create(entry_id=duplicate.id, tag_name=t)
        if readme:
            Tag.objects.get_or_create(entry_id=duplicate.id, tag_name='readme')
        entry_url = request.build_absolute_uri(reverse('entry_detail', args=[duplicate.id]))
        entry_link = f"[{title}]({entry_url})" if title else entry_url
        return JsonResponse({
            "status": "duplicate",
            "entry_id": duplicate.id,
            "content": duplicate.content,
            "entry_url": entry_url,
            "entry_link": entry_link,
            "updated": True,
        })

    now = time.time()
    entry = Entry.objects.create(
        id=str(uuid.uuid7()),
        content=content,
        kind='bookmark',
        name=inline_name,
        context_id=inline_context,
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )
    Tag.objects.create(tag_name='chrome', entry=entry)
    for t in inline_tags:
        Tag.objects.get_or_create(entry_id=entry.id, tag_name=t)
    if readme:
        Tag.objects.create(tag_name='readme', entry=entry)

    from .tagger import tag_bookmark
    auto_tags = tag_bookmark(entry)

    entry_url = request.build_absolute_uri(reverse('entry_detail', args=[entry.id]))
    entry_link = f"[{title}]({entry_url})" if title else entry_url
    return JsonResponse({
        "status": "ok",
        "entry_id": entry.id,
        "content": content,
        "entry_url": entry_url,
        "entry_link": entry_link,
        "auto_tags": auto_tags,
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_curate_page(request):
    """Stage a page the user is viewing (+ optional fetched PDFs) and dispatch
    the deterministic picks curator (scripts/curate_page.py).

    Called by the tj-getlink Chrome extension's "Curate picks from this page"
    buttons. The extension runs inside the user's authenticated session, so for
    the "with-download" variant it fetches the page's same-origin PDFs itself
    and uploads them here — the server is never authenticated to the source site.

    Requires Bearer token matching SysConfig 'gmail_addon_api_key'.

    multipart/form-data fields:
      url        (required) the page URL
      title      page title
      source     human label for the page (e.g. workshop name)
      mode       'page' (text only) | 'download' (text + uploaded PDFs)
      page_text  extracted page text
      pdfs       (download mode) one or more uploaded PDF files

    Returns {status:'queued', job_id}. Curation runs in the background; created
    picks appear in the normal /tjai/picks/ triage UI.
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({"error": "Authorization required"}, status=401)
    token = auth_header[7:]
    try:
        api_key = SysConfig.objects.get(key='gmail_addon_api_key').value
    except SysConfig.DoesNotExist:
        return JsonResponse({"error": "API key not configured"}, status=503)
    if token != api_key:
        return JsonResponse({"error": "Invalid API key"}, status=403)

    import os
    import sys
    import subprocess
    from pathlib import Path

    url = (request.POST.get('url') or '').strip()
    if not url:
        return JsonResponse({"error": "url is required"}, status=400)
    title = (request.POST.get('title') or '').strip()
    source = (request.POST.get('source') or '').strip()
    mode = (request.POST.get('mode') or 'page').strip()
    page_text = request.POST.get('page_text') or ''

    pdf_files = request.FILES.getlist('pdfs')
    MAX_PDFS = 25  # server-side cap; the extension also warns/approves past a threshold
    truncated = len(pdf_files) > MAX_PDFS
    pdf_files = pdf_files[:MAX_PDFS]

    # Stage the job on disk for the background worker.
    # HANDOFF (ec2dev): confirm this root is writable by the web user; falls back
    # to the repo's data/ dir if /var/www is not writable in this deployment.
    scripts_dir = Path(__file__).resolve().parent.parent / 'scripts'
    jobs_root = Path('/var/www/tjai/data/curate-jobs')
    try:
        jobs_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        jobs_root = scripts_dir.parent / 'data' / 'curate-jobs'
        jobs_root.mkdir(parents=True, exist_ok=True)

    job_id = str(uuid.uuid7())
    job_dir = jobs_root / job_id
    (job_dir / 'pdfs').mkdir(parents=True, exist_ok=True)

    (job_dir / 'meta.json').write_text(
        json.dumps({'url': url, 'title': title, 'source': source, 'mode': mode}),
        encoding='utf-8')
    if page_text.strip():
        (job_dir / 'page.txt').write_text(page_text, encoding='utf-8')

    saved = 0
    if mode == 'download':
        for f in pdf_files:
            name = os.path.basename(f.name or '') or f'deck-{saved}.pdf'
            if not name.lower().endswith('.pdf'):
                name += '.pdf'
            with open(job_dir / 'pdfs' / name, 'wb') as out:
                for chunk in f.chunks():
                    out.write(chunk)
            saved += 1

    # Fire-and-forget: curate_page.py reads job_dir, runs claude -p, and creates
    # the picks. The HTTP request returns immediately.
    log_fh = open(job_dir / 'job.log', 'wb')
    subprocess.Popen(
        [sys.executable, str(scripts_dir / 'curate_page.py'), str(job_dir)],
        stdout=log_fh, stderr=log_fh, start_new_session=True,
    )
    SysConfig.objects.update_or_create(
        key=f'curate_{job_id}_status',
        defaults={'value': 'running', 'timestamp_modified': time.time()})

    return JsonResponse({
        'status': 'queued',
        'job_id': job_id,
        'mode': mode,
        'pdfs_saved': saved,
        'pdfs_truncated': truncated,
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_add_journal(request):
    """Create a journal entry from an external source (Gmail Add-on, Chrome extension).

    Requires Bearer token matching SysConfig 'gmail_addon_api_key'.

    Request body: {title, event_timestamp, location, zoom_url, gmail_url,
                   indico_url, event_url, source (default: "gmail")}

    event_url is a generic source link (e.g. a web event page detected by the
    Chrome extension); indico_url is the Indico-specific equivalent.
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({"error": "Authorization required"}, status=401)
    token = auth_header[7:]

    try:
        api_key = SysConfig.objects.get(key='gmail_addon_api_key').value
    except SysConfig.DoesNotExist:
        return JsonResponse({"error": "API key not configured"}, status=503)

    if token != api_key:
        return JsonResponse({"error": "Invalid API key"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    title = data.get("title", "").strip()
    event_timestamp = data.get("event_timestamp")
    zoom_url = data.get("zoom_url", "").strip()
    gmail_url = data.get("gmail_url", "").strip()
    indico_url = data.get("indico_url", "").strip()
    event_url = data.get("event_url", "").strip()
    location = data.get("location", "").strip()
    source = data.get("source", "gmail").strip()

    if not title:
        return JsonResponse({"error": "title is required"}, status=400)
    if not isinstance(event_timestamp, (int, float)):
        return JsonResponse({"error": "event_timestamp must be a number"}, status=400)

    parts = [title]
    if location:
        parts.append(f"@ {location}")
    if zoom_url:
        parts.append(f"[zoom]({zoom_url})")
    if gmail_url:
        parts.append(f"[gmail]({gmail_url})")
    if indico_url:
        parts.append(f"[indico]({indico_url})")
    if event_url:
        parts.append(f"[event]({event_url})")
    content = " ".join(parts)

    now = time.time()
    entry = Entry.objects.create(
        id=str(uuid.uuid7()),
        content=content,
        kind='journal',
        data={'event_date': float(event_timestamp)},
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )
    Tag.objects.create(tag_name=source, entry=entry)

    return JsonResponse({
        "status": "ok",
        "entry_id": entry.id,
        "content": content,
    })


@csrf_exempt
@require_http_methods(["POST"])
@login_required
@csrf_exempt
def api_diary_today(request):
    """Find or create a diary entry. Accepts optional ?date=YYYY-MM-DD for retroactive creation."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    from .tjai_utils import get_app_tz
    from datetime import datetime, date as date_type
    tz = get_app_tz()
    date_str = request.GET.get('date') or request.POST.get('date')
    if date_str:
        try:
            today = date_type.fromisoformat(date_str)
        except ValueError:
            return JsonResponse({'error': 'Invalid date format, use YYYY-MM-DD'}, status=400)
    else:
        today = datetime.now(tz).date()
    entry_id = f'diary-{today.isoformat()}'
    existing = Entry.objects.filter(
        data__entry_id=entry_id, deleted_at__isnull=True,
    ).first()
    if existing:
        return JsonResponse({'entry_id': entry_id, 'id': str(existing.id)})
    # Create new diary entry
    now = time.time()
    title = today.strftime('Diary: %a %b %-d, %Y')
    event_date = today.strftime('%Y%m%d')
    from . import services
    result = services.create_entry(
        content=title,
        kind='journal',
        context='diary',
        event_date=event_date,
        event_time='0000',
        data={'entry_id': entry_id},
    )
    if isinstance(result, dict) and 'error' in result:
        return JsonResponse(result, status=400)
    return JsonResponse({'entry_id': entry_id, 'id': result.get('id', '')})


@login_required
@csrf_exempt
def api_entry_create(request):
    """Create a new entry and return its UUID for redirect to edit page.

    Accepts optional JSON body to seed the entry:
        content, kind, context, data, tags (list of strings)
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    import uuid
    import time
    now = time.time()
    body = {}
    if request.body:
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            pass
    entry = Entry.objects.create(
        id=str(uuid.uuid7()),
        content=body.get('content', ''),
        kind=body.get('kind', 'memory'),
        context_id=body.get('context') or None,
        data=body.get('data') or None,
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )
    for tag_name in (body.get('tags') or []):
        Tag.objects.create(tag_name=tag_name, entry_id=entry.id)
    return JsonResponse({'id': str(entry.id)})


@csrf_exempt
@require_http_methods(["POST"])
def api_log(request):
    """Write to AppLog from external sources. Requires Bearer token.

    Request body: {source, message, level, extra_data}
    level: debug/info/warning/error (default: debug)
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({"error": "Authorization required"}, status=401)
    token = auth_header[7:]
    try:
        api_key = SysConfig.objects.get(key='gmail_addon_api_key').value
    except SysConfig.DoesNotExist:
        return JsonResponse({"error": "API key not configured"}, status=503)
    if token != api_key:
        return JsonResponse({"error": "Invalid API key"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    message = data.get("message", "").strip()
    if not message:
        return JsonResponse({"error": "message is required"}, status=400)

    level_map = {'debug': logging.DEBUG, 'info': logging.INFO, 'warning': logging.WARNING, 'error': logging.ERROR}
    level_str = data.get("level", "debug").lower()
    level = level_map.get(level_str, logging.DEBUG)

    from django.utils import timezone as tz
    AppLog.objects.create(
        source=data.get("source", "external"),
        timestamp=tz.now(),
        level=level,
        levelname=logging.getLevelName(level),
        message=message,
        extra_data=data.get("extra_data"),
    )
    return JsonResponse({"status": "ok"})


@csrf_exempt
def api_add_entry(request):
    """Create a generic entry from an external source (Gmail addon, etaverse debug).

    Requires Bearer token matching SysConfig 'gmail_addon_api_key'.

    Request body: {kind, content, tags, context, source}
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({"error": "Authorization required"}, status=401)
    token = auth_header[7:]

    try:
        api_key = SysConfig.objects.get(key='gmail_addon_api_key').value
    except SysConfig.DoesNotExist:
        return JsonResponse({"error": "API key not configured"}, status=503)

    if token != api_key:
        return JsonResponse({"error": "Invalid API key"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    kind = data.get("kind", "memory").strip()
    content = data.get("content", "").strip()
    tags_str = data.get("tags", "").strip()
    context_name = data.get("context", "").strip() or None
    source = data.get("source", "gmail").strip()
    event_date = data.get("event_date", "").strip() if isinstance(data.get("event_date"), str) else ""
    event_time = data.get("event_time", "").strip() if isinstance(data.get("event_time"), str) else ""

    if not content:
        return JsonResponse({"error": "content is required"}, status=400)

    context_obj = None
    if context_name:
        context_obj = Context.objects.filter(name=context_name).first()

    # For journal entries, convert YYYYMMDD + optional HHMM to timestamp for data.event_date
    all_day = data.get("all_day", False)
    entry_data = {}
    if kind == 'journal' and event_date:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            app_tz = ZoneInfo('America/New_York')
            if all_day:
                # Midnight Eastern = standard all-day convention
                dt = datetime.strptime(event_date, '%Y%m%d').replace(tzinfo=app_tz)
            else:
                dt = datetime.strptime(event_date, '%Y%m%d').replace(tzinfo=app_tz)
                if event_time and len(event_time) == 4:
                    dt = dt.replace(hour=int(event_time[:2]), minute=int(event_time[2:]))
            entry_data['event_date'] = dt.timestamp()
        except ValueError:
            return JsonResponse({"error": "event_date must be YYYYMMDD format"}, status=400)

    now = time.time()
    entry = Entry.objects.create(
        id=str(uuid.uuid7()),
        content=content,
        kind=kind,
        context=context_obj,
        data=entry_data,
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )
    Tag.objects.create(tag_name=source, entry=entry)
    if tags_str:
        for tag in tags_str.split(','):
            tag = tag.strip()
            if tag:
                Tag.objects.create(tag_name=tag, entry=entry)

    return JsonResponse({
        "status": "ok",
        "entry_id": entry.id,
        "content": content,
    })


@csrf_exempt
@require_http_methods(["GET", "POST"])
@login_required
def api_dialog_daily_counts(request):
    """Return daily dialog turn counts for the full collection period."""
    try:
        tz = get_app_tz()
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT (to_timestamp(timestamp_modified) AT TIME ZONE %s)::date AS day,
                       count(*) AS cnt
                FROM entries
                WHERE id IN (
                    SELECT entry_id FROM tags WHERE tag_name = %s
                ) AND deleted_at IS NULL
                      AND (status IS NULL OR status != 'archive')
                GROUP BY day ORDER BY day
            """, [str(tz), DIALOG_TAG])
            rows = [{'date': row[0].isoformat(), 'count': row[1]}
                    for row in cursor.fetchall()]
        return JsonResponse({'daily_counts': rows})
    except Exception as e:
        logger.exception("api_dialog_daily_counts failed")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_dialog(request):
    """Record and retrieve AI assistant dialog turns.

    GET: Return recent dialog entries (query param: turns, default 20).
    POST: Record a dialog turn.

    Requires Bearer token matching SysConfig 'gmail_addon_api_key'.
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({"error": "Authorization required"}, status=401)
    token = auth_header[7:]

    try:
        api_key = SysConfig.objects.get(key='gmail_addon_api_key').value
    except SysConfig.DoesNotExist:
        return JsonResponse({"error": "API key not configured"}, status=503)

    if token != api_key:
        return JsonResponse({"error": "Invalid API key"}, status=403)

    if request.method == "GET":
        turns = int(request.GET.get("turns", 20))
        hostname = request.GET.get("hostname", "").strip()
        entry_ids = Tag.objects.filter(
            tag_name=DIALOG_TAG
        ).values_list('entry_id', flat=True)
        qs = Entry.objects.filter(
            id__in=entry_ids,
            deleted_at__isnull=True,
        )
        if hostname:
            qs = qs.filter(data__hostname=hostname)
        entries = list(qs.order_by('-timestamp_created')[:turns])
        entries.reverse()
        result = []
        for e in entries:
            data = e.data if isinstance(e.data, dict) else {}
            result.append({
                "id": e.id,
                "content": e.content[:2000] + (f"\n[truncated — full entry: {e.id}]" if len(e.content) > 2000 else ""),
                "role": data.get("role", "unknown"),
                "client": data.get("client"),
                "model": data.get("model"),
                "model_provider": data.get("model_provider"),
                "reasoning_effort": data.get("reasoning_effort"),
                "session_id": data.get("session_id"),
                "project_path": data.get("project_path"),
                "hostname": data.get("hostname"),
                "timestamp": e.timestamp_created,
            })
        return JsonResponse({"status": "ok", "entries": result})

    # POST
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    content = data.get("content", "").strip()
    role = data.get("role", "").strip()

    if not content:
        return JsonResponse({"error": "content is required"}, status=400)
    if role not in ("user", "assistant"):
        return JsonResponse({"error": "role must be 'user' or 'assistant'"}, status=400)

    now = time.time()
    entry_data = {
        "role": role,
        "client": data.get("client"),
        "model": data.get("model"),
        "model_provider": data.get("model_provider"),
        "reasoning_effort": data.get("reasoning_effort"),
        "session_id": data.get("session_id"),
        "project_path": data.get("project_path"),
        "hostname": data.get("hostname"),
    }

    extra_tags = []

    # Detect research subagent products
    if content.startswith('<task-notification>'):
        summary_m = re.search(r'<summary>(.*?)</summary>', content, re.DOTALL)
        if summary_m:
            summary_text = summary_m.group(1).strip()
            active_uuid = SysConfig.objects.filter(
                key='agent_research-agent_entry'
            ).values_list('value', flat=True).first()
            if active_uuid:
                source = Entry.objects.filter(
                    id=active_uuid, deleted_at__isnull=True
                ).first()
                if source:
                    source_eid = (source.data or {}).get('entry_id', '')
                    entry_data['source_entry_id'] = source_eid
                    entry_data['source_uuid'] = active_uuid
                    slug = re.sub(r'[^a-z0-9]+', '-',
                                  summary_text.lower().replace('agent ', '')
                                  .replace('"', '').replace('completed', '')
                                  .strip()).strip('-')[:40]
                    if source_eid and slug:
                        entry_data['entry_id'] = f'{source_eid}:{slug}'
                    extra_tags.append('research-subagent')

    Context.objects.get_or_create(
        name=CURRENT_DIALOG_CONTEXT,
        defaults={
            'title': 'Co-development dialog',
            'description': 'AI pair-programming and co-development dialog across clients',
            'timestamp_created': now,
            'timestamp_modified': now,
        },
    )
    entry = Entry.objects.create(
        id=str(uuid.uuid7()),
        content=content,
        kind='memory',
        context_id=CURRENT_DIALOG_CONTEXT,
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=0,
        data=entry_data,
    )
    Tag.objects.create(tag_name=DIALOG_TAG, entry=entry)
    for tag in extra_tags:
        Tag.objects.create(tag_name=tag, entry=entry)

    return JsonResponse({"status": "ok", "entry_id": entry.id})


@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE"])
def api_kozy_chat(request):
    """KozyKorner persistent chat.

    GET: Return recent messages. Query params: limit (default 50), before (id for pagination).
    POST: Send a message. Body: {sender, content}

    Requires Bearer token matching SysConfig 'gmail_addon_api_key'.
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({"error": "Authorization required"}, status=401)
    token = auth_header[7:]

    try:
        api_key = SysConfig.objects.get(key='gmail_addon_api_key').value
    except SysConfig.DoesNotExist:
        return JsonResponse({"error": "API key not configured"}, status=503)

    if token != api_key:
        return JsonResponse({"error": "Invalid API key"}, status=403)

    if request.method == "GET":
        limit = int(request.GET.get("limit", 50))
        before = request.GET.get("before")
        sender = request.GET.get("sender", "").strip()
        search = request.GET.get("q", "").strip()
        qs = KozyChat.objects.all()
        if sender:
            qs = qs.filter(sender=sender)
        if search:
            qs = qs.filter(content__icontains=search)
        if before:
            qs = qs.filter(id__lt=int(before))
        messages = list(qs.order_by('-id')[:limit])
        messages.reverse()
        return JsonResponse({"messages": [
            {
                "id": m.id,
                "ts": m.timestamp.isoformat(),
                "sender": m.sender,
                "content": m.content,
            } for m in messages
        ]})

    # DELETE
    if request.method == "DELETE":
        msg_id = request.GET.get("id")
        if not msg_id:
            return JsonResponse({"error": "id required"}, status=400)
        deleted, _ = KozyChat.objects.filter(id=msg_id).delete()
        return JsonResponse({"ok": deleted > 0})

    # POST
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    sender = (data.get("sender") or "").strip()
    content = (data.get("content") or "").strip()
    if not sender or not content:
        return JsonResponse({"error": "sender and content required"}, status=400)

    msg = KozyChat.objects.create(sender=sender, content=content)
    return JsonResponse({
        "id": msg.id,
        "ts": msg.timestamp.isoformat(),
        "sender": msg.sender,
        "content": msg.content,
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_bulk_import(request):
    """Bulk import bookmarks from external sources.

    Requires Bearer token matching SysConfig 'gmail_addon_api_key'.

    Request body: {
        "items": [
            {"content": "[Title](url)", "tags": ["tag1"], "timestamp": 1234567890.0},
            ...
        ],
        "source_tag": "dynalist",  // optional, added to all entries
        "skip_existing": true,     // optional, default true
        "create_context": false    // optional, default false
    }

    Response: {
        "imported": 100,
        "skipped": 5,
        "errors": ["Item 3: empty content"],
        "auto_tags": {"recipe": 10, "video": 3}
    }
    """
    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return JsonResponse({"error": "Authorization required"}, status=401)
    token = auth_header[7:]

    try:
        api_key = SysConfig.objects.get(key='gmail_addon_api_key').value
    except SysConfig.DoesNotExist:
        return JsonResponse({"error": "API key not configured"}, status=503)

    if token != api_key:
        return JsonResponse({"error": "Invalid API key"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    items = data.get("items", [])
    if not items:
        return JsonResponse({"error": "items array is required"}, status=400)
    if not isinstance(items, list):
        return JsonResponse({"error": "items must be an array"}, status=400)

    source_tag = data.get("source_tag")
    skip_existing = data.get("skip_existing", True)
    create_context = data.get("create_context", False)

    from bulk_import.loader import bulk_import_bookmarks
    results = bulk_import_bookmarks(
        items,
        source_tag=source_tag,
        skip_existing=skip_existing,
        create_context=create_context,
    )

    return JsonResponse(results)


# --- Telegram Mini App ---

@xframe_options_exempt
def miniapp(request):
    """Serve the Telegram Mini App."""
    return render(request, 'tjai_app/miniapp.html')


@csrf_exempt
@require_http_methods(["POST"])
def tg_auth(request):
    """Validate Telegram initData and create a Django session."""
    import hashlib
    import hmac
    import urllib.parse

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    init_data = data.get("initData", "")
    if not init_data:
        return JsonResponse({"error": "initData required"}, status=400)

    bot_token = django_settings.TELEGRAM_BOT_TOKEN
    if not bot_token:
        return JsonResponse({"error": "Bot token not configured"}, status=503)

    # Parse initData into key-value pairs
    params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    received_hash = params.pop("hash", "")
    if not received_hash:
        return JsonResponse({"error": "Missing hash"}, status=400)

    # Build data-check-string: sorted key=value pairs joined by \n
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(params.items())
    )

    # HMAC validation per Telegram docs
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return JsonResponse({"error": "Invalid hash"}, status=403)

    # Check auth_date is recent (within 1 hour)
    auth_date = int(params.get("auth_date", 0))
    if abs(time.time() - auth_date) > 3600:
        return JsonResponse({"error": "Auth data expired"}, status=403)

    # Verify user ID matches configured owner
    try:
        user_data = json.loads(params.get("user", "{}"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid user data"}, status=400)

    tg_user_id = str(user_data.get("id", ""))
    allowed_id = django_settings.TELEGRAM_USER_ID
    if not allowed_id or tg_user_id != allowed_id:
        return JsonResponse({"error": "Unauthorized user"}, status=403)

    # Create Django session for the admin user
    from django.contrib.auth.models import User
    user = User.objects.filter(is_superuser=True).first()
    if not user:
        return JsonResponse({"error": "No admin user"}, status=500)

    login(request, user)
    return JsonResponse({"status": "ok", "user": user_data.get("first_name", "")})


@login_required
def api_contexts_list(request):
    """Return contexts with entry counts as JSON."""
    from django.db.models import Count, Q
    contexts = (
        Context.objects
        .annotate(entry_count=Count(
            'entry',
            filter=Q(entry__deleted_at__isnull=True)
        ))
        .filter(entry_count__gt=0)
        .order_by('name')
    )
    result = [
        {"name": c.name, "title": c.title, "count": c.entry_count}
        for c in contexts
    ]
    return JsonResponse({"contexts": result})


@login_required
def api_context_entries(request, context_name):
    """Return entries for a context as JSON."""
    entries = Entry.objects.filter(
        context_id=context_name,
        deleted_at__isnull=True,
    ).exclude(status='archive').order_by('-timestamp_modified')[:1000]

    entry_ids = [e.id for e in entries]
    tags_by_entry = {}
    for t in Tag.objects.filter(entry_id__in=entry_ids):
        tags_by_entry.setdefault(t.entry_id, []).append(t.tag_name)

    result = []
    for e in entries:
        lines = e.content.split('\n')
        data = e.data if isinstance(e.data, dict) else None
        line_count = len([l for l in lines if l.strip()])
        entry_tags = tags_by_entry.get(e.id, [])
        missing_tags = [t for t in entry_tags if f':{t}' not in e.content]
        result.append({
            'id': str(e.id),
            'content': lines[0],
            'kind': e.kind,
            'context': e.context_id,
            'timestamp': e.timestamp_modified,
            'date_display': fmt_datetime(e.timestamp_modified),
            'line_count': line_count if line_count > 1 else None,
            'name': e.name,
            'nickname': data.get('nickname') if data else None,
            'event_date': data.get('event_date') if data else None,
            'tags': missing_tags,
        })

    return JsonResponse({'entries': result})


@login_required
def api_entry_content(request, entry_id):
    """Return full entry content as JSON."""
    entry = Entry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if not entry:
        return JsonResponse({'error': 'Not found'}, status=404)

    tags = list(Tag.objects.filter(entry_id=entry.id).values_list('tag_name', flat=True))
    data = entry.data if isinstance(entry.data, dict) else None

    response = JsonResponse({
        'id': str(entry.id),
        'content': entry.content,
        'kind': entry.kind,
        'context': entry.context_id,
        'name': entry.name,
        'entry_id': data.get('entry_id') if data else None,
        'timestamp': entry.timestamp_modified,
        'tags': tags,
        'event_date': data.get('event_date') if data else None,
    })
    response['Cache-Control'] = 'no-store, max-age=0'
    return response


@login_required
def picks(request):
    """Render the picks triage page."""
    return render(request, 'tjai_app/picks.html')


# --- Research ---

@login_required
def research_page(request):
    """Render the compact research topic list page."""
    return render(request, 'tjai_app/research_list.html')


@login_required
def research_list_page(request):
    """Render the compact research topic list page."""
    return render(request, 'tjai_app/research_list.html')


@login_required
def research_obsolete_page(request):
    """Render the obsolete research queue page (superseded by research_page /
    research_list.html). Kept reachable but not for new work."""
    return render(request, 'tjai_app/research.html')


@login_required
def research_detail_page(request, detail_entry_id=None):
    """Render one research topic's full detail page."""
    return render(request, 'tjai_app/research_detail.html', {
        'detail_entry_id': detail_entry_id or '',
    })


def research_service_worker(request):
    """Serve the research service worker at /tjai/ scope."""
    sw_path = django_settings.BASE_DIR / 'tjai_app' / 'static' / 'tjai' / 'research-sw.js'
    response = HttpResponse(
        sw_path.read_text(),
        content_type='application/javascript; charset=utf-8',
    )
    response['Service-Worker-Allowed'] = '/tjai/'
    response['Cache-Control'] = 'no-cache'
    return response


def offline_service_worker(request):
    """Serve the generic offline worker."""
    sw_path = django_settings.BASE_DIR / 'tjai_app' / 'static' / 'tjai' / 'offline-sw.js'
    response = HttpResponse(
        sw_path.read_text(),
        content_type='application/javascript; charset=utf-8',
    )
    response['Service-Worker-Allowed'] = '/tjai/'
    response['Cache-Control'] = 'no-cache'
    return response


def _research_summary_text(content, limit=420):
    lines = [line.strip() for line in (content or '').splitlines()]
    raw_title = next((line for line in lines if line), 'Untitled research topic')
    title = _research_clean_title(raw_title)
    body_lines = []
    seen_title = False
    for line in lines:
        if not seen_title:
            if line == raw_title:
                seen_title = True
            continue
        if not line:
            if body_lines:
                break
            continue
        if re.match(r'^#{1,6}\s+', line):
            continue
        clean_line = _research_plain_summary(line)
        if not clean_line:
            continue
        if re.match(r'^(territory|territory to explore|why now|status)\b[:.]?$', clean_line, re.IGNORECASE):
            continue
        if re.match(r'^status:\s*', clean_line, re.IGNORECASE):
            continue
        body_lines.append(line)
    description = _research_plain_summary(' '.join(body_lines))
    if len(description) > limit:
        description = description[:limit - 3].rstrip() + '...'
    return title, description


def _research_plain_summary(text):
    text = re.sub(r'^#{1,6}\s+', '', text or '')
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    return re.sub(r'\s+', ' ', text).strip()


def _research_clean_title(title):
    return re.sub(r'^#{1,6}\s+', '', title or '').strip() or 'Untitled research topic'


def _research_title_and_territory(content):
    lines = (content or '').splitlines()
    title_index = None
    title = 'Untitled research topic'
    for idx, line in enumerate(lines):
        if line.strip():
            title_index = idx
            title = _research_clean_title(line.strip())
            break
    if title_index is None:
        return title, ''
    territory = '\n'.join(lines[title_index + 1:]).strip()
    return title, territory


def _research_display_status(entry, data):
    status = entry.status or data.get('status') or 'pending'
    if status == 'pending' and data.get('source') == 'ideation-agent':
        return 'proposed'
    return status


def _research_topic_has_started(data, model_map=None, subentry_count=0,
                                queued=False, agent_running=False):
    """Return True once a base research topic has real execution state.

    Ideation-created topics should sit as proposed until explicitly submitted.
    A model can mistakenly create them with Entry.status='active'; that status
    only means "running" for base topics after dispatch, which always stamps
    started_at or model/run metadata.
    """
    if queued or agent_running or subentry_count:
        return True
    if any(data.get(k) for k in (
        'started_at', 'run_status', 'run_completed_at', 'run_error',
        'synthesis_done', 'synthesis_triggered',
    )):
        return True
    if any(v for v in (model_map or {}).values()):
        return True
    for key, value in data.items():
        if value and (key.endswith('_status') or key.endswith('_entry_id')):
            return True
    return False


def _research_corrected_base_status(entry, data, model_map=None,
                                    subentry_count=0, queued=False,
                                    agent_running=False):
    display_status = _research_display_status(entry, data)
    if (
        display_status == 'active'
        and data.get('source') == 'ideation-agent'
        and not _research_topic_has_started(
            data,
            model_map=model_map,
            subentry_count=subentry_count,
            queued=queued,
            agent_running=agent_running,
        )
    ):
        return 'proposed'
    return display_status


def _research_execution_info(entry, data):
    info = []
    if data.get('run_status'):
        info.append(f"run {data.get('run_status')}")
    if data.get('run_exit_code') is not None:
        info.append(f"exit {data.get('run_exit_code')}")
    if data.get('run_duration_seconds') is not None:
        try:
            info.append(fmt_duration(int(float(data.get('run_duration_seconds')))))
        except (TypeError, ValueError):
            info.append(f"{data.get('run_duration_seconds')}s")
    if data.get('worker_target'):
        info.append(f"worker target {data.get('worker_target')}")
    if data.get('worker_machine_id'):
        info.append(f"worker {data.get('worker_machine_id')}")
    if data.get('worker_duration_sec') is not None:
        try:
            info.append(f"worker {fmt_duration(int(float(data.get('worker_duration_sec'))))}")
        except (TypeError, ValueError):
            info.append(f"worker {data.get('worker_duration_sec')}s")
    if data.get('worker_claimed_by'):
        info.append(f"claimed by {data.get('worker_claimed_by')}")
    if data.get('worker_claimed_at'):
        try:
            info.append(f"claimed {fmt_datetime(float(data.get('worker_claimed_at')))}")
        except (TypeError, ValueError):
            pass
    if data.get('worker_staged_at'):
        try:
            info.append(f"staged {fmt_datetime(float(data.get('worker_staged_at')))}")
        except (TypeError, ValueError):
            pass
    if data.get('run_completed_at'):
        try:
            info.append(f"completed {fmt_datetime(float(data.get('run_completed_at')))}")
        except (TypeError, ValueError):
            pass
    if data.get('content_lines') is not None:
        info.append(f"{data.get('content_lines')} lines")
    if data.get('run_error'):
        info.append(f"error: {str(data.get('run_error')).splitlines()[0]}")
    return info


def _research_branch_status(entry, data):
    status = entry.status or 'pending'
    if (
        status == 'active'
        and data.get('source') == 'multimodel'
        and data.get('worker_target')
        and not data.get('worker_claimed_at')
    ):
        return 'staged'
    return status


@login_required
def api_research_list(request):
    """Return compact base research topics for the new list page."""
    from .action_runner import RESEARCH_MODELS

    def _int_param(name, default, min_value, max_value):
        try:
            value = int(request.GET.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(min_value, min(max_value, value))

    q = (request.GET.get('q') or '').strip().lower()
    scope = request.GET.get('scope') or 'title'
    if scope not in ('title', 'topic', 'bodies'):
        scope = 'title'
    status_param = request.GET.get('status') or 'open'
    include_archived = status_param == 'all'
    status_filter = set()
    if status_param not in ('open', 'all'):
        status_filter = {
            s.strip() for s in status_param.split(',')
            if s.strip()
        }
    offset = _int_param('offset', 0, 0, 100000)
    limit = _int_param('limit', 100, 1, 300)

    research_ids = Tag.objects.filter(
        tag_name='research_topic'
    ).values_list('entry_id', flat=True)
    base_entries = list(Entry.objects.filter(
        id__in=research_ids,
        kind='memory',
        deleted_at__isnull=True,
    ).exclude(
        data__source='multimodel'
    ).order_by('-timestamp_modified'))

    base_by_entry_id = {}
    for entry in base_entries:
        data = entry.data if isinstance(entry.data, dict) else {}
        eid = data.get('entry_id')
        if eid:
            base_by_entry_id[eid] = entry

    model_statuses = {}
    # Per-topic queue position (1-based) and item kind from research-agent's
    # pending_runs. Kind distinguishes 'run'/'rerun' (the topic itself is
    # queued) from 'synthesize' (the topic is done research, its synthesis
    # is queued).
    queued_position_by_eid = {}
    queued_kind_by_eid = {}
    ra = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='research-agent',
    ).first()
    if ra:
        pending = (ra.data or {}).get('pending_runs') or []
        for idx, item in enumerate(pending, start=1):
            eid = item.get('entry_id')
            if eid and eid not in queued_position_by_eid:
                queued_position_by_eid[eid] = idx
                queued_kind_by_eid[eid] = item.get('kind') or 'run'

    subentry_counts = Counter()
    researched_models = {}
    model_errors = {}
    synthesis_done = set()
    synthesis_status_by_eid = {}
    for sub in Entry.objects.filter(
        data__source='multimodel',
        deleted_at__isnull=True,
    ).only('id', 'status', 'data'):
        data = sub.data if isinstance(sub.data, dict) else {}
        beid = data.get('base_entry_id')
        if not beid or beid not in base_by_entry_id:
            continue
        subentry_counts[beid] += 1
        model = data.get('model')
        if model:
            status = sub.status or 'pending'
            model_statuses.setdefault(beid, {})[model] = status
            if model == 'synthesis':
                if status == 'done':
                    synthesis_done.add(beid)
                synthesis_status_by_eid[beid] = status
            else:
                researched_models.setdefault(beid, []).append(model)
            if data.get('run_error'):
                model_errors.setdefault(beid, {})[model] = data.get('run_error')

    body_matches = set()
    if q and scope == 'bodies':
        for sub in Entry.objects.filter(
            data__source='multimodel',
            deleted_at__isnull=True,
        ).only('content', 'data'):
            data = sub.data if isinstance(sub.data, dict) else {}
            beid = data.get('base_entry_id')
            if beid and beid in base_by_entry_id and q in (sub.content or '').lower():
                body_matches.add(beid)

    items = []
    counts_by_status = Counter()
    # Top activity panel summary. Filled across ALL topics below (before the
    # filter/pagination cut) so it never misses an in-flight, queued, or
    # synthesis-owed topic regardless of the current search/status filter or page.
    activity = {'active': [], 'queued': [], 'pending_synth': []}
    for entry in base_entries:
        data = entry.data if isinstance(entry.data, dict) else {}
        eid = data.get('entry_id') or ''
        title, description = _research_summary_text(entry.content)
        display_status = _research_display_status(entry, data)

        model_map = {}
        for model in RESEARCH_MODELS:
            model_map[model] = (
                data.get(f'{model}_status')
                or model_statuses.get(eid, {}).get(model)
                or None
            )
        display_status = _research_corrected_base_status(
            entry,
            data,
            model_map=model_map,
            subentry_count=subentry_counts.get(eid, 0),
            queued=eid in queued_position_by_eid,
        )
        model_counts = Counter(v for v in model_map.values() if v)
        _active_models = [m for m in RESEARCH_MODELS
                          if model_map.get(m) in ('launching', 'active', 'staged', 'rerun')]
        _synth_is_done = eid in synthesis_done or data.get('synthesis_done') is True
        # Synthesis is "in flight" whenever its sub-entry exists and is not
        # terminal. _create_and_dispatch_synthesis creates the sub-entry with
        # NO status (None) and only stamps 'done' on completion — so a
        # None/pending status means running, NOT owed. Only done/failed/blocked
        # are terminal. Testing the status string for 'active'/'staged' (as the
        # old code did) silently misses the entire run.
        _synth_in_flight = (eid in synthesis_status_by_eid and not _synth_is_done
                            and synthesis_status_by_eid.get(eid) not in ('failed', 'blocked'))
        if _active_models:
            display_status = 'active'
        elif _synth_in_flight:
            # Models done, synthesis in flight — topic is NOT done yet.
            display_status = 'active'
        elif any(v == 'failed' for v in model_map.values()) and display_status in ('proposed', 'pending'):
            display_status = 'failed'
        counts_by_status[display_status] += 1

        # Activity panel classification (mutually exclusive, priority order).
        # Done before the filter continues so it covers every topic.
        _detail_url = f'/tjai/research-detail/{eid}_detail/'
        if _active_models or _synth_in_flight:
            activity['active'].append({
                'entry_id': eid, 'detail_url': _detail_url,
                'phase': 'researching' if _active_models else 'synthesizing',
                'models': _active_models,
            })
        elif eid in queued_position_by_eid:
            activity['queued'].append({
                'entry_id': eid, 'detail_url': _detail_url,
                'kind': queued_kind_by_eid.get(eid) or 'run',
            })
        elif researched_models.get(eid) and not _synth_is_done:
            activity['pending_synth'].append({
                'entry_id': eid, 'detail_url': _detail_url,
            })

        if status_filter and display_status not in status_filter:
            continue
        if not status_filter and not include_archived and display_status == 'archive':
            continue
        match_scope = None
        if q:
            title_match = q in title.lower()
            topic_match = q in (entry.content or '').lower()
            body_match = eid in body_matches
            if scope == 'title' and not title_match:
                continue
            if scope == 'topic' and not topic_match:
                continue
            if scope == 'bodies' and not (topic_match or body_match):
                continue
            match_scope = 'body' if body_match and not topic_match else 'topic'
            if title_match:
                match_scope = 'title'

        items.append({
            'id': str(entry.id),
            'entry_id': eid,
            'title': title,
            'description': description,
            'status': display_status,
            'raw_status': entry.status,
            'priority': entry.priority,
            'source': data.get('source') or '',
            'created': entry.timestamp_created,
            'created_display': fmt_datetime(entry.timestamp_created),
            'modified': entry.timestamp_modified,
            'modified_display': fmt_datetime(entry.timestamp_modified),
            'modified_ago': fmt_ago(entry.timestamp_modified),
            'models': model_map,
            'model_counts': dict(model_counts),
            'model_errors': model_errors.get(eid, {}),
            'researched_models': sorted(set(researched_models.get(eid, []))),
            'synthesis_done': eid in synthesis_done or data.get('synthesis_done') is True,
            'synthesis_status': synthesis_status_by_eid.get(eid),
            'queued_position': queued_position_by_eid.get(eid),
            'queued_kind': queued_kind_by_eid.get(eid),
            'subentry_count': subentry_counts.get(eid, 0),
            'match_scope': match_scope,
            'detail_entry_id': f'{eid}_detail',
            'detail_url': f'/tjai/research-detail/{eid}_detail/',
            'entry_url': f'/tjai/entry/?entry_id={eid}',
            'studies_url': f'/tjai/research/studies/?entry_id={eid}',
        })

    total_filtered = len(items)
    page_items = items[offset:offset + limit]
    response = JsonResponse({
        'items': page_items,
        'models': list(RESEARCH_MODELS),
        'activity': activity,
        'stats': {
            'topics': len(base_entries),
            'filtered': total_filtered,
            'returned': len(page_items),
            'offset': offset,
            'limit': limit,
            'has_more': offset + limit < total_filtered,
            'counts_by_status': dict(counts_by_status),
        },
    })
    response['Cache-Control'] = 'no-store, max-age=0'
    return response


@login_required
def api_research_detail(request):
    """Return one research topic with full territory, branches, and synthesis."""
    from .action_runner import RESEARCH_MODELS

    entry_id_param = request.GET.get('entry_id')
    if entry_id_param and entry_id_param.endswith('_detail'):
        entry_id_param = entry_id_param[:-len('_detail')]
    research_ids = Tag.objects.filter(
        tag_name='research_topic'
    ).values_list('entry_id', flat=True)
    qs = Entry.objects.filter(
        id__in=research_ids,
        kind='memory',
        deleted_at__isnull=True,
    ).exclude(
        data__source='multimodel'
    )
    if entry_id_param:
        entry = qs.filter(data__entry_id=entry_id_param).first()
    else:
        return JsonResponse({'error': 'entry_id required'}, status=400)
    if not entry:
        return JsonResponse({'error': 'Research topic not found'}, status=404)

    data = entry.data if isinstance(entry.data, dict) else {}
    eid = data.get('entry_id') or ''
    title, territory = _research_title_and_territory(entry.content)
    _, description = _research_summary_text(entry.content)
    agent_current_entry = SysConfig.objects.filter(
        key='agent_research-agent_entry'
    ).values_list('value', flat=True).first()
    agent_status = SysConfig.objects.filter(
        key='agent_research-agent_status'
    ).values_list('value', flat=True).first()
    branch_by_model = {}

    branches = []
    synthesis = None
    study_ids = Tag.objects.filter(
        tag_name='research-subagent'
    ).values_list('entry_id', flat=True)
    studies_count = Entry.objects.filter(
        id__in=study_ids,
        deleted_at__isnull=True,
        data__source_uuid=str(entry.id),
    ).count()
    subentries = Entry.objects.filter(
        data__source='multimodel',
        data__base_entry_id=eid,
        deleted_at__isnull=True,
    ).order_by('timestamp_created')
    for sub in subentries:
        sdata = sub.data if isinstance(sub.data, dict) else {}
        model = sdata.get('model') or ''
        display_status = _research_branch_status(sub, sdata)
        item = {
            'id': str(sub.id),
            'entry_id': sdata.get('entry_id') or '',
            'model': model,
            'status': display_status,
            'raw_status': sub.status or 'pending',
            'source': sdata.get('source') or '',
            'content': sub.content,
            'content_html': _linkify_rendered_html(_render_markdown(sub.content)),
            'content_chars': len(sub.content or ''),
            'modified_ago': fmt_ago(sub.timestamp_modified),
            'modified_display': fmt_datetime(sub.timestamp_modified),
            'created_display': fmt_datetime(sub.timestamp_created),
            'run_error': sdata.get('run_error') or '',
            'execution_info': _research_execution_info(sub, sdata),
            'worker_target': sdata.get('worker_target') or '',
            'worker_staged_at': sdata.get('worker_staged_at'),
            'worker_claimed_at': sdata.get('worker_claimed_at'),
            'worker_claimed_by': sdata.get('worker_claimed_by') or '',
            'entry_url': f"/tjai/entry/?entry_id={sdata.get('entry_id') or ''}",
        }
        if model == 'synthesis':
            synthesis = item
        else:
            branches.append(item)
            if model:
                branch_by_model[model] = item

    model_statuses = {}
    model_errors = {}
    for model in RESEARCH_MODELS:
        model_statuses[model] = (
            (branch_by_model.get(model) or {}).get('status')
            or data.get(f'{model}_status')
        )
        if (branch_by_model.get(model) or {}).get('run_error'):
            model_errors[model] = branch_by_model[model]['run_error']
    topic_status = _research_corrected_base_status(
        entry,
        data,
        model_map=model_statuses,
        subentry_count=len(branches) + (1 if synthesis else 0),
        agent_running=agent_status == 'running' and agent_current_entry == str(entry.id),
    )

    response = JsonResponse({
        'topic': {
            'id': str(entry.id),
            'entry_id': eid,
            'title': title,
            'description': description,
            'territory': territory,
            'territory_html': _linkify_rendered_html(_render_markdown(territory or entry.content)),
            'content': entry.content,
            'content_html': _linkify_rendered_html(_render_markdown(entry.content)),
            'status': topic_status,
            'raw_status': entry.status,
            'priority': entry.priority,
            'source': data.get('source') or '',
            'created_display': fmt_datetime(entry.timestamp_created),
            'modified_display': fmt_datetime(entry.timestamp_modified),
            'modified_ago': fmt_ago(entry.timestamp_modified),
            'detail_entry_id': f'{eid}_detail',
            'entry_url': f'/tjai/entry/?entry_id={eid}',
            'studies_url': f'/tjai/research/studies/?entry_id={eid}',
            'studies_count': studies_count,
            'agent_running_here': agent_status == 'running' and agent_current_entry == str(entry.id),
        },
        'models': list(RESEARCH_MODELS),
        'model_statuses': model_statuses,
        'model_errors': model_errors,
        'agent': {
            'status': agent_status or 'idle',
            'current_entry': agent_current_entry or '',
            'running': agent_status == 'running',
            'running_here': agent_status == 'running' and agent_current_entry == str(entry.id),
        },
        'branches': branches,
        'synthesis': synthesis,
    })
    response['Cache-Control'] = 'no-store, max-age=0'
    return response


@login_required
def api_research_data(request):
    """Return research queue entries and agent status as JSON."""
    from .action_runner import RESEARCH_MODELS, heal_research_subprocess_state
    heal_research_subprocess_state()
    research_ids = Tag.objects.filter(
        tag_name='research_topic'
    ).values_list('entry_id', flat=True)
    entries = Entry.objects.filter(
        id__in=research_ids,
        kind='memory',
        deleted_at__isnull=True,
    ).order_by('-timestamp_modified')

    # Build two logically independent indexes off two separate queries.
    # Mixing them into one query bit us twice: once with conflated L1/L4
    # state this morning, and again when external (non-research) work
    # started flowing through the same worker pipeline — the worker
    # claim index was scoped to source='multimodel' and went blind to
    # codoc jobs, so a busy worker rendered as 'idle'.
    #
    # Index A — model_entries_by_base: per-research-topic display. Only
    # research sub-entries have base_entry_id + model on their data, so
    # this is correctly scoped to source='multimodel'. Remote-worker
    # fields are attached when present.
    #
    # Index B — claims_by_machine_cap / claims_by_cap: WORKER health.
    # A remote worker is busy iff it holds ANY active claim, regardless
    # of which subsystem staged the work. This query must be scoped
    # ONLY by worker_target (and not-deleted), never by source.
    model_entries_by_base = {}
    claims_by_machine = {}  # machine_id -> all claims it holds (any cap)

    # Index A: research-only, for per-topic breakdown.
    for sub in Entry.objects.filter(
        data__source='multimodel',
        deleted_at__isnull=True,
    ):
        sd = sub.data if isinstance(sub.data, dict) else {}
        beid = sd.get('base_entry_id')
        if not beid:
            continue
        cap = sd.get('worker_target')
        staged_at = sd.get('worker_staged_at')
        claimed_at = sd.get('worker_claimed_at')
        claimed_by = sd.get('worker_claimed_by')
        info = {
            'model': sd.get('model'),
            'worker_target': cap,
            'worker_staged_at': staged_at,
            'worker_staged_ago': fmt_ago(float(staged_at)) if staged_at else None,
            'worker_claimed_at': claimed_at,
            'worker_claimed_ago': fmt_ago(float(claimed_at)) if claimed_at else None,
            'worker_claimed_by': claimed_by,
            'base_entry_id': beid,
            'sub_status': sub.status,
            'entry_id': sd.get('entry_id'),
            'run_error': sd.get('run_error'),
        }
        model_entries_by_base.setdefault(beid, []).append(info)

    # Index B: ALL active remote-worker entries, source-agnostic. This
    # must include codoc/external submissions or the worker-health
    # derivation will lie about idle workers that are actually busy.
    external_work_in_flight = []
    for sub in Entry.objects.filter(
        data__worker_target__isnull=False,
        deleted_at__isnull=True,
    ):
        sd = sub.data if isinstance(sub.data, dict) else {}
        cap = sd.get('worker_target')
        claimed_at = sd.get('worker_claimed_at')
        claimed_by = sd.get('worker_claimed_by')
        staged_at = sd.get('worker_staged_at')
        if not (claimed_by and claimed_at and cap):
            continue
        info = {
            'worker_target': cap,
            'worker_claimed_at': claimed_at,
            'worker_claimed_ago': fmt_ago(float(claimed_at)) if claimed_at else None,
            'worker_claimed_by': claimed_by,
            'base_entry_id': sd.get('base_entry_id'),
            'source': sd.get('source'),
            'external_label': sd.get('external_label'),
        }
        claims_by_machine.setdefault(claimed_by, []).append(info)
        # Surface non-research work on the page so a busy worker has
        # a visible explanation alongside the state dot.
        if sd.get('source') != 'multimodel':
            external_work_in_flight.append({
                'source': sd.get('source') or '(unknown)',
                'label': sd.get('external_label') or '',
                'capability': cap,
                'claimed_by': claimed_by,
                'claimed_at_ago': fmt_ago(float(claimed_at)) if claimed_at else None,
                'staged_at_ago': fmt_ago(float(staged_at)) if staged_at else None,
            })

    items = []
    for e in entries:
        data = e.data if isinstance(e.data, dict) else {}
        item = {
            'id': str(e.id),
            'entry_id': data.get('entry_id', ''),
            'content': e.content,
            'status': e.status or 'pending',
            'priority': e.priority,
            'created': e.timestamp_created,
            'created_display': fmt_datetime(e.timestamp_created),
            'modified': e.timestamp_modified,
            'modified_ago': fmt_ago(e.timestamp_modified),
            'started_at': data.get('started_at'),
            'source': data.get('source'),
        }
        # Per-model status for all active research models — driven by
        # RESEARCH_MODELS so adding a model doesn't require UI edits.
        for m in RESEARCH_MODELS:
            item[f'{m}_status'] = data.get(f'{m}_status')
            sa = data.get(f'{m}_started_at')
            item[f'{m}_started_at'] = sa
            item[f'{m}_started_at_ago'] = fmt_ago(float(sa)) if sa else None
        # Remote-worker sub-entry: Entry.status='active' spans both "staged
        # but unclaimed" and "claim-held-running". Expose worker_target +
        # worker_claimed_at so the UI can distinguish.
        if data.get('source') == 'multimodel' and data.get('worker_target'):
            item['worker_target'] = data.get('worker_target')
            item['worker_claimed_at'] = data.get('worker_claimed_at')
        # Attach remote-worker sub-entry info if any
        beid = data.get('entry_id')
        if beid and beid in model_entries_by_base:
            item['workers'] = model_entries_by_base[beid]
            for sub in model_entries_by_base[beid]:
                model = sub.get('model')
                run_error = sub.get('run_error')
                if model and run_error:
                    item[f'{model}_error'] = run_error
                    item[f'{model}_subentry_id'] = sub.get('entry_id')
        items.append(item)

    # --- Top activity panel (MAIN PAGE ONLY) -----------------------------
    # Additive read-only summary for research.html's prominent top panel.
    # Detail pages are served by api_research_detail and are NOT affected.
    # Real work state straight from the data — synthesis sub-entry presence
    # + status, base model statuses, and the run queue. Computed across ALL
    # topics (this endpoint is unpaginated) so nothing in flight or owed is
    # missed. No agent status flag, no system_busy.
    _synth_exists = set()       # base eids that have a synthesis sub-entry
    _synth_done = set()         # base eids whose synthesis sub-entry is done
    _synth_terminal = set()     # base eids whose synthesis is terminal (done/failed/blocked)
    _research_done = set()      # base eids with >=1 completed model report
    for _beid, _subs in model_entries_by_base.items():
        for _sub in _subs:
            if _sub.get('model') == 'synthesis':
                _synth_exists.add(_beid)
                if _sub.get('sub_status') == 'done':
                    _synth_done.add(_beid)
                if _sub.get('sub_status') in ('done', 'failed', 'blocked'):
                    _synth_terminal.add(_beid)
            elif _sub.get('sub_status') == 'done':
                _research_done.add(_beid)

    _queued_kind = {}
    try:
        from .research_queue import _research_action
        _ra = _research_action()
        for _p in (((_ra.data or {}).get('pending_runs') or []) if _ra else []):
            _peid = _p.get('entry_id')
            if _peid and _peid not in _queued_kind:
                _queued_kind[_peid] = _p.get('kind') or 'run'
    except Exception as _exc:
        logger.warning("activity panel: queue read failed: %s", _exc)

    activity = {'active': [], 'queued': [], 'pending_synth': []}
    for _it in items:
        if _it.get('source') == 'multimodel':
            continue
        _eid = _it.get('entry_id') or ''
        _detail_url = f'/tjai/research-detail/{_eid}_detail/'
        _active_models = [m for m in RESEARCH_MODELS
                          if _it.get(f'{m}_status') in ('active', 'launching')]
        # A failed/blocked synthesis is terminal, not in flight — otherwise a
        # synthesis that never wrote its report shows "synthesizing" forever
        # (mirrors the list activity panel's already-correct check above).
        _synth_in_flight = _eid in _synth_exists and _eid not in _synth_terminal
        if _active_models or _synth_in_flight:
            _phase = 'synthesizing' if (_synth_in_flight and not _active_models) else 'researching'
            activity['active'].append({
                'entry_id': _eid, 'detail_url': _detail_url,
                'phase': _phase, 'models': _active_models})
        elif _eid in _queued_kind:
            activity['queued'].append({
                'entry_id': _eid, 'detail_url': _detail_url,
                'kind': _queued_kind[_eid]})
        elif _eid in _research_done and _eid not in _synth_done:
            activity['pending_synth'].append({
                'entry_id': _eid, 'detail_url': _detail_url})

    # Worker health — derived PER MACHINE, not per capability.
    #
    # Why per-machine: the `worker_capability_{cap}_lastpoll` sysconfig rows
    # are a denormalized view of a per-machine fact. `worker_poll` writes
    # one row per advertised cap in a single request's loop, all with the
    # SAME timestamp. A worker advertising caps {A, B} always has
    # identical lastpoll rows for A and B. Deriving "disconnected" per cap
    # from per-cap timestamps treats correlated data as independent and
    # produces contradictions the moment the machine holds a claim on one
    # cap (which blocks its poll loop, making BOTH caps' timestamps go
    # stale together) — cap A reads "busy" via its claim while cap B
    # reads "disconnected" via its (identical) stale timestamp.
    #
    # Aliveness is a property of the MACHINE. A capability is alive iff
    # at least one machine advertising it is alive. So we group by
    # machine, derive one liveness state per machine, and list its
    # advertised caps alongside.
    #
    # States (per machine):
    #   busy         — machine holds at least one active claim (any cap)
    #                  whose age is within WORKER_CLAIM_STALE_SECONDS.
    #   zombie       — machine holds a claim whose age exceeds
    #                  WORKER_CLAIM_STALE_SECONDS. Server's auto-reclaim
    #                  on the next poll will fix it.
    #   idle         — no claim held AND last poll within
    #                  WORKER_DISCONNECTED_SECONDS.
    #   disconnected — no claim held AND last poll older than that.
    #   unknown      — never polled (should not happen for a machine that
    #                  has a claim on record; included for completeness).
    WORKER_DISCONNECTED_SECONDS = 180
    now_ts = time.time()

    # machines: machine_id -> {advertised_caps set, last_poll_ts, claims list}
    machines = {}
    sc_rows = list(SysConfig.objects.filter(
        key__startswith='worker_capability_', key__endswith='_lastpoll'))
    for sc in sc_rows:
        cap = sc.key[len('worker_capability_'):-len('_lastpoll')]
        try:
            info = json.loads(sc.value) if sc.value else {}
        except (ValueError, TypeError):
            info = {}
        mid = info.get('machine_id')
        ts = info.get('ts')
        if not mid:
            continue
        m = machines.setdefault(mid, {
            'machine_id': mid,
            'caps': set(),
            'last_poll_ts': None,
            'claims': [],
        })
        m['caps'].add(cap)
        # All this machine's per-cap rows have the same ts (see comment),
        # but tolerate skew if the invariant ever breaks: take the newest.
        if ts is not None:
            try:
                ts_f = float(ts)
                if m['last_poll_ts'] is None or ts_f > m['last_poll_ts']:
                    m['last_poll_ts'] = ts_f
            except (TypeError, ValueError):
                pass

    # Fold claims in — both attach to the machine row and expose the cap
    # the claim is against (which may differ from the "busy on which cap"
    # display when a machine advertises multiple).
    for mid, claim_list in claims_by_machine.items():
        m = machines.setdefault(mid, {
            'machine_id': mid,
            'caps': set(),
            'last_poll_ts': None,
            'claims': [],
        })
        m['claims'].extend(claim_list)
        # If a claim exists on a cap this machine is not (currently)
        # advertising via sysconfig — e.g. the machine stopped offering
        # the cap mid-inference — still include the cap so the display
        # can say "busy on <cap>".
        for c in claim_list:
            if c.get('worker_target'):
                m['caps'].add(c['worker_target'])

    workers = []
    for mid in sorted(machines):
        m = machines[mid]
        last_ts = m['last_poll_ts']
        age_sec = (now_ts - last_ts) if last_ts else None
        fresh_claims = []
        zombie_claims = []
        for c in m['claims']:
            cat = c.get('worker_claimed_at')
            try:
                cat_f = float(cat) if cat else 0
            except (TypeError, ValueError):
                cat_f = 0
            if cat_f and (now_ts - cat_f) <= WORKER_CLAIM_STALE_SECONDS:
                fresh_claims.append(c)
            else:
                zombie_claims.append(c)

        busy_on_cap = None
        busy_on_entry = None
        busy_on_source = None
        busy_on_label = None
        busy_since_ago = None
        if fresh_claims:
            state = 'busy'
            top = max(fresh_claims, key=lambda c: c.get('worker_claimed_at') or 0)
            busy_on_cap = top.get('worker_target')
            busy_on_entry = top.get('base_entry_id')
            busy_on_source = top.get('source')
            busy_on_label = top.get('external_label')
            busy_since_ago = top.get('worker_claimed_ago')
        elif zombie_claims:
            state = 'zombie'
            top = max(zombie_claims, key=lambda c: c.get('worker_claimed_at') or 0)
            busy_on_cap = top.get('worker_target')
            busy_on_entry = top.get('base_entry_id')
            busy_on_source = top.get('source')
            busy_on_label = top.get('external_label')
            busy_since_ago = top.get('worker_claimed_ago')
        elif last_ts is None:
            state = 'unknown'
        elif age_sec is not None and age_sec <= WORKER_DISCONNECTED_SECONDS:
            state = 'idle'
        else:
            state = 'disconnected'

        workers.append({
            'machine_id': mid,
            'caps': sorted(m['caps']),
            'last_poll_ts': last_ts,
            'last_poll_ago': fmt_ago(last_ts) if last_ts else None,
            'age_sec': age_sec,
            'state': state,
            'busy_on_cap': busy_on_cap,
            'busy_on_entry': busy_on_entry,
            'busy_on_source': busy_on_source,
            'busy_on_label': busy_on_label,
            'busy_since_ago': busy_since_ago,
        })

    # System prompt and ideation prompt entry UUIDs
    sysprompt = Entry.objects.filter(
        data__entry_id='research-system-prompt',
        deleted_at__isnull=True,
    ).values_list('id', flat=True).first()
    ideation_prompt = Entry.objects.filter(
        data__entry_id='ideation-agent',
        deleted_at__isnull=True,
    ).values_list('id', flat=True).first()

    # Agent status from sysconfig (with stale detection + zombie killing)
    agent_keys = {}
    for sc in SysConfig.objects.filter(key__startswith='agent_research-agent'):
        agent_keys[sc.key] = sc.value

    status_val, launched_val = _heal_stale_agent(
        'agent_research-agent_status',
        'agent_research-agent_launched',
        'research agent',
    )

    def _epoch_ago(val):
        """Convert sysconfig epoch string to 'Xm ago' display."""
        if not val:
            return ''
        try:
            return fmt_ago(float(val))
        except (ValueError, TypeError):
            return ''

    def _epoch_dur(val):
        """Convert sysconfig epoch string to duration-from-now (no 'ago')."""
        if not val:
            return ''
        try:
            return fmt_duration(int(time.time() - float(val)))
        except (ValueError, TypeError):
            return ''

    def _epoch_age_sec(val):
        """Seconds since epoch string, for threshold checks."""
        if not val:
            return None
        try:
            return int(time.time() - float(val))
        except (ValueError, TypeError):
            return None

    launched_epoch = launched_val or agent_keys.get('agent_research-agent_launched')
    completed_epoch = agent_keys.get('agent_research-agent_completed')
    last_activity_epoch = agent_keys.get('agent_research-agent_last_activity')
    last_error_time_epoch = agent_keys.get('agent_research-agent_last_error_time')

    agent_status = {
        'status': status_val,
        'launched': launched_epoch,
        'launched_ago': _epoch_ago(launched_epoch),
        'launched_dur': _epoch_dur(launched_epoch),
        'completed': completed_epoch,
        'completed_ago': _epoch_ago(completed_epoch),
        'tracking': agent_keys.get('agent_research-agent_tracking'),
        'current_entry': agent_keys.get('agent_research-agent_entry'),
        'last_activity': last_activity_epoch,
        'last_activity_ago': _epoch_ago(last_activity_epoch),
        'last_activity_age': _epoch_age_sec(last_activity_epoch),
        'process_alive': agent_keys.get('agent_research-agent_process_alive'),
        'health': agent_keys.get('agent_research-agent_health'),
        'last_error': agent_keys.get('agent_research-agent_last_error'),
        'last_error_time': last_error_time_epoch,
        'last_error_time_ago': _epoch_ago(last_error_time_epoch),
    }

    # One-bit "is something legitimately progressing in the system" flag.
    # Zombie claims are NOT busy — they are leftovers from a dead worker.
    local_model_busy = any(
        item.get('source') != 'multimodel'
        and any(item.get(f'{m}_status') in ('active', 'launching')
                for m in RESEARCH_MODELS)
        for item in items
    )
    system_busy = (
        status_val == 'running'
        or any(w.get('state') == 'busy' for w in workers)
        or local_model_busy
    )

    return JsonResponse({
        'items': items,
        'models': list(RESEARCH_MODELS),
        'workers': workers,
        'external_work_in_flight': external_work_in_flight,
        'system_busy': system_busy,
        'sysprompt_id': str(sysprompt) if sysprompt else None,
        'ideation_prompt_id': str(ideation_prompt) if ideation_prompt else None,
        'agent': agent_status,
        'activity': activity,
    })


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_research_run(request):
    """Trigger research run — same pattern as api_picks_run."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    entry_id = body.get('entry_id')
    if not entry_id:
        logger.error("api_research_run: no entry_id in request body")
        return JsonResponse({'error': 'entry_id required'}, status=400)

    # Find research-agent action entry (presence check; producers never write
    # next_target — drain does, via dispatch_run).
    research_action = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='research-agent',
    ).first()
    if not research_action:
        logger.error("api_research_run: research-agent action entry not found in DB")
        return JsonResponse({'error': 'research-agent action not found'}, status=404)

    if entry_id != 'all':
        target = Entry.objects.filter(
            data__entry_id=entry_id, deleted_at__isnull=True
        ).first()
        if not target:
            logger.error("api_research_run: no entry with data.entry_id=%r", entry_id)
            return JsonResponse({'error': f'Research entry not found: {entry_id}'}, status=404)
        if target.status == 'active':
            return JsonResponse({'error': 'Research already in progress for this entry'}, status=409)
        target_uuid = str(target.id)
        resolved_entry_id = entry_id
    else:
        # Legacy "all" — enqueue the highest-priority pending primary topic.
        research_ids = Tag.objects.filter(
            tag_name='research_topic'
        ).values_list('entry_id', flat=True)
        first_item = Entry.objects.filter(
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
            data__has_key='run_status'
        ).order_by('priority', 'timestamp_created').first()
        if not first_item:
            return JsonResponse({'error': 'No pending research items'}, status=400)
        target_uuid = str(first_item.id)
        resolved_entry_id = (first_item.data or {}).get('entry_id') or target_uuid

    from .research_queue import enqueue, drain_if_idle
    pos = enqueue('run', target_uuid, resolved_entry_id)
    drained = drain_if_idle()

    _log_research(logging.INFO,
                  f"Run enqueued: {resolved_entry_id} position={pos}"
                  + (" (drained immediately)" if drained else ""),
                  entry_id=target_uuid)

    return JsonResponse({'ok': True, 'queued': True, 'position': pos,
                         'entry_id': resolved_entry_id,
                         'drained': bool(drained)}, status=202)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_research_stop(request):
    """Legacy soft stop: cross-topic chaining is already disabled."""
    status = SysConfig.objects.filter(
        key='agent_research-agent_status'
    ).values_list('value', flat=True).first()
    if status != 'running':
        return JsonResponse({'error': 'Research agent not running'}, status=409)
    return JsonResponse({'ok': True, 'message': 'Cross-topic chaining is disabled'})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_research_abort(request):
    """Hard abort: kill processes immediately and reset status."""
    return _abort_agent('agent_research-agent_status', 'research agent')


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_research_rerun(request):
    """Create a versioned copy of a completed research entry for re-research."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    entry_id = body.get('entry_id')  # human-readable, e.g. "research-agent-context-paradox"
    if not entry_id:
        return JsonResponse({'error': 'entry_id required'}, status=400)

    # Find original entry
    original = Entry.objects.filter(
        data__entry_id=entry_id, deleted_at__isnull=True,
    ).first()
    if not original:
        return JsonResponse({'error': f'Entry not found: {entry_id}'}, status=404)

    # Extract topic (first line of content, before any report)
    topic = original.content.split('\n')[0].strip()

    # Determine next version number by searching for existing versions
    base_id = re.sub(r'-v\d+$', '', entry_id)  # strip existing -vN suffix
    existing = Entry.objects.filter(
        deleted_at__isnull=True,
        data__entry_id__startswith=base_id + '-v',
    ).values_list('data__entry_id', flat=True)
    max_ver = 1  # original is implicitly v1
    for eid in existing:
        m = re.search(r'-v(\d+)$', eid or '')
        if m:
            max_ver = max(max_ver, int(m.group(1)))
    next_ver = max_ver + 1
    new_entry_id = f'{base_id}-v{next_ver}'

    # Create the new versioned entry
    now = time.time()
    context_obj = original.context
    new_entry = Entry.objects.create(
        id=str(uuid.uuid7()),
        content=topic,
        kind='memory',
        context=context_obj,
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
        data={
            'entry_id': new_entry_id,
            'source': 'rerun',
            'original_entry_id': entry_id,
            'original_uuid': str(original.id),
            'version': next_ver,
        },
    )
    Tag.objects.create(tag_name='research_topic', entry=new_entry)

    _log_research(logging.INFO,
                  f"Rerun created: {new_entry_id} from {entry_id}",
                  entry_id=str(new_entry.id))

    # Auto-submit: enqueue the new entry. Drain picks it up immediately if
    # the agent is idle, or after the running agent completes otherwise.
    research_action = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='research-agent',
    ).first()
    auto_submitted = False
    queue_position = None
    drained = False
    if research_action:
        from .research_queue import enqueue, drain_if_idle
        queue_position = enqueue('run', str(new_entry.id), new_entry_id)
        drained = bool(drain_if_idle())
        _log_research(logging.INFO,
                      f"Rerun enqueued: {new_entry_id} position={queue_position}"
                      + (" (drained immediately)" if drained else ""),
                      entry_id=str(new_entry.id))
        auto_submitted = True

    return JsonResponse({
        'ok': True,
        'new_entry_id': new_entry_id,
        'new_uuid': str(new_entry.id),
        'version': next_ver,
        'auto_submitted': auto_submitted,
        'queue_position': queue_position,
        'drained': drained,
    })


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_research_restage_subentry(request):
    """Re-stage a remote-worker sub-entry: rebuild the prompt from the current
    per-model system-prompt entry, reset worker_staged_at, clear any stale
    claim. Lets a staged sub-entry pick up prompt edits without doing a full
    rerun of the topic. Only valid for multimodel sub-entries with a
    worker_target (i.e. gemma/qwen-class remote-worker entries).
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    sub_uuid = body.get('uuid')
    sub_entry_id = body.get('entry_id')
    if not sub_uuid and not sub_entry_id:
        return JsonResponse({'error': 'entry_id required'}, status=400)
    sub = None
    if sub_entry_id:
        sub = Entry.objects.filter(
            data__entry_id=sub_entry_id, deleted_at__isnull=True,
        ).first()
    if not sub and sub_uuid:
        sub = Entry.objects.filter(id=sub_uuid, deleted_at__isnull=True).first()
    if not sub:
        return JsonResponse({'error': 'sub-entry not found'}, status=404)
    sd = sub.data if isinstance(sub.data, dict) else {}
    if sd.get('source') != 'multimodel' or not sd.get('worker_target'):
        return JsonResponse({'error': 'not a remote-worker sub-entry'}, status=400)
    model = sd.get('model')
    base_eid = sd.get('base_entry_id')
    if not (model and base_eid):
        return JsonResponse({'error': 'model or base_entry_id missing on sub-entry'}, status=400)
    base = Entry.objects.filter(
        data__entry_id=base_eid, deleted_at__isnull=True,
    ).first()
    if not base:
        return JsonResponse({'error': f'base entry {base_eid} not found'}, status=404)
    from .action_runner import build_research_prompt
    topic_text = (base.content or '').split('\n')[0].strip()
    try:
        new_prompt = build_research_prompt(topic_text, model)
    except Exception as e:
        return JsonResponse({'error': f'prompt build failed: {e}'}, status=500)
    now = time.time()
    sd['worker_prompt'] = new_prompt
    sd['worker_staged_at'] = now
    sd.pop('worker_claimed_by', None)
    sd.pop('worker_claimed_at', None)
    sub.data = sd
    sub.timestamp_modified = now
    sub.save(update_fields=['data', 'timestamp_modified'])
    # Reset base tracking to 'staged' in case it was 'active' under a stale
    # claim — the next poll will flip it back to 'active' atomically.
    bd = base.data if isinstance(base.data, dict) else {}
    bd[f'{model}_status'] = 'staged'
    base.data = bd
    base.timestamp_modified = now
    base.save(update_fields=['data', 'timestamp_modified'])
    return JsonResponse({'status': 'ok', 'prompt_chars': len(new_prompt), 'model': model})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_research_rerun_models(request):
    """Rerun selected models for a completed research topic.

    Sets selected models' status to 'rerun' on the base entry.
    Deletes old model entries so they're recreated fresh.
    Triggers the research-agent to dispatch only the 'rerun' models.
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    entry_id = body.get('entry_id')
    models = body.get('models', [])  # e.g. ['claude', 'gemini']
    if not entry_id:
        return JsonResponse({'error': 'entry_id required'}, status=400)

    from .action_runner import RESEARCH_MODELS
    valid_models = set(RESEARCH_MODELS)
    models = [m for m in models if m in valid_models]
    if not models:
        return JsonResponse({'error': 'No valid models selected'}, status=400)

    base = Entry.objects.filter(
        data__entry_id=entry_id, deleted_at__isnull=True,
    ).first()
    if not base:
        return JsonResponse({'error': f'Entry not found: {entry_id}'}, status=404)

    from .research_queue import enqueue, drain_if_idle
    pos = enqueue('rerun', str(base.id), entry_id, models=models)
    drained = bool(drain_if_idle())

    _log_research(logging.INFO,
                  f"Model rerun enqueued: {entry_id} models={models} position={pos}"
                  + (" (drained immediately)" if drained else ""),
                  entry_id=str(base.id))

    return JsonResponse({
        'ok': True,
        'queued': True,
        'position': pos,
        'entry_id': entry_id,
        'models': models,
        'drained': drained,
    }, status=202)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_research_synthesize(request):
    """Manually enqueue the synthesis step for a research topic.

    Always-live button — the human decides when synthesis is appropriate.
    Enqueues a 'synthesize' item; drain dispatches via dispatch_synthesize,
    which retires any existing synthesis sub-entry and calls
    _create_and_dispatch_synthesis (single writer to next_target)."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    entry_id = body.get('entry_id')
    if not entry_id:
        return JsonResponse({'error': 'entry_id required'}, status=400)

    base = Entry.objects.filter(
        data__entry_id=entry_id, deleted_at__isnull=True,
    ).first()
    if not base:
        return JsonResponse({'error': f'Entry not found: {entry_id}'}, status=404)

    from .action_runner import RESEARCH_MODELS
    base_data = base.data if isinstance(base.data, dict) else {}
    dispatched = [m for m in RESEARCH_MODELS
                  if base_data.get(f'{m}_entry_id')]
    if not dispatched:
        return JsonResponse(
            {'error': 'No dispatched models on this topic — nothing to synthesize'},
            status=400)

    from .research_queue import enqueue, drain_if_idle
    pos = enqueue('synthesize', str(base.id), entry_id)
    drained = bool(drain_if_idle())

    synth_entry_id = f'{entry_id}-synthesis'
    _log_research(logging.INFO,
                  f"Synthesis enqueued: {entry_id} models={dispatched} position={pos}"
                  + (" (drained immediately)" if drained else ""),
                  entry_id=str(base.id))

    return JsonResponse({
        'ok': True,
        'queued': True,
        'position': pos,
        'entry_id': entry_id,
        'synthesis_entry_id': synth_entry_id,
        'models': dispatched,
        'drained': drained,
    }, status=202)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_picks_abort(request):
    """Hard abort: kill processes immediately and reset status."""
    return _abort_agent('agent_picks-agent_status', 'picks agent')


def _abort_agent(status_key, agent_name):
    """Request abort of a running agent. Resets status and requests process kill.

    Cannot kill processes directly because Apache (www-data) lacks permission
    to signal admin's processes. Sets a sysconfig flag that the action agent
    daemon picks up to do the actual kill.
    """
    status = SysConfig.objects.filter(
        key=status_key
    ).values_list('value', flat=True).first()
    if status != 'running':
        return JsonResponse({'error': f'{agent_name} not running'}, status=409)

    now = time.time()
    SysConfig.objects.update_or_create(
        key=status_key,
        defaults={'value': 'idle', 'timestamp_modified': now})
    # Request the action agent daemon to kill zombie processes
    SysConfig.objects.update_or_create(
        key='agent_kill_requested',
        defaults={'value': '1', 'timestamp_modified': now})
    logger.warning("Abort requested for %s, status reset to idle", agent_name)
    return JsonResponse({'ok': True})


@login_required
def research_studies(request):
    """Page showing subagent entries produced during a research topic."""
    return render(request, 'tjai_app/research_studies.html')


@login_required
def api_research_studies(request):
    """Return subagent entries for a specific research topic."""
    topic_uuid = request.GET.get('uuid', '').strip()
    topic_entry_id = request.GET.get('entry_id', '').strip()
    if topic_entry_id.endswith('_detail'):
        topic_entry_id = topic_entry_id[:-len('_detail')]
    if not topic_uuid and not topic_entry_id:
        return JsonResponse({'error': 'entry_id required'}, status=400)

    # Get the parent research entry for display info
    parent = None
    if topic_entry_id:
        parent = Entry.objects.filter(
            data__entry_id=topic_entry_id, deleted_at__isnull=True,
        ).first()
        if parent:
            topic_uuid = str(parent.id)
    if not parent and topic_uuid:
        parent = Entry.objects.filter(
            id=topic_uuid, deleted_at__isnull=True,
        ).first()
    parent_info = None
    if parent:
        pdata = parent.data if isinstance(parent.data, dict) else {}
        parent_info = {
            'id': str(parent.id),
            'entry_id': pdata.get('entry_id', ''),
            'content': parent.content.split('\n')[0],
        }

    # Find subagent entries: tagged research-subagent with source_uuid matching
    sub_ids = Tag.objects.filter(
        tag_name='research-subagent'
    ).values_list('entry_id', flat=True)

    entries = Entry.objects.filter(
        id__in=sub_ids,
        deleted_at__isnull=True,
        data__source_uuid=topic_uuid,
    ).order_by('-timestamp_modified')

    items = []
    for e in entries:
        data = e.data if isinstance(e.data, dict) else {}
        items.append({
            'id': str(e.id),
            'entry_id': data.get('entry_id', ''),
            'content': e.content or '',
            'kind': e.kind,
            'created': e.timestamp_created,
            'modified': e.timestamp_modified,
            'modified_display': fmt_datetime(e.timestamp_modified),
            'context': e.context.name if e.context else None,
        })

    return JsonResponse({
        'parent': parent_info,
        'items': items,
    })


@login_required
def api_picks_data(request):
    """Return picks grouped by run as JSON."""
    entries = Entry.objects.filter(
        kind='bookmark',
        context__name='picks',
        deleted_at__isnull=True,
    ).exclude(
        tags__tag_name='source',
    ).order_by('-timestamp_created')

    runs = {}
    run_meta = {}
    for e in entries:
        data = e.data if isinstance(e.data, dict) else {}
        run_key = data.get('run', 'unknown')
        if run_key not in runs:
            runs[run_key] = []
            run_meta[run_key] = {'timestamps': [], 'sources': set()}
        run_meta[run_key]['timestamps'].append(e.timestamp_created)
        source = data.get('source', '')
        if source:
            run_meta[run_key]['sources'].add(source)
        # Parse markdown link: [Title](url)
        content = e.content or ''
        title = content
        url = ''
        if content.startswith('[') and '](' in content:
            title = content[1:content.index('](')]
            url = content[content.index('](') + 2:].rstrip(')')
        runs[run_key].append({
            'id': e.id,
            'title': title,
            'url': url,
            'source': data.get('source', ''),
            'precis': data.get('precis', ''),
            'rationale': data.get('rationale', ''),
            'thumbs': data.get('thumbs'),
            'kept': data.get('kept', False),
            'archived': data.get('archived', False),
            'readme': data.get('readme', False),
        })

    # Sort runs by key descending (ISO timestamps sort correctly)
    sorted_runs = []
    for run_key in sorted(runs.keys(), reverse=True):
        meta = run_meta.get(run_key, {})
        timestamps = meta.get('timestamps', [])
        duration = None
        if len(timestamps) >= 2:
            duration = round(max(timestamps) - min(timestamps))
        sorted_runs.append({
            'run': run_key,
            'run_display': fmt_datetime(run_key) if run_key != 'unknown' else 'Unknown run',
            'picks': runs[run_key],
            'duration_seconds': duration,
            'duration_display': fmt_duration(duration) if duration else None,
            'source_count': len(meta.get('sources', set())),
        })

    # Picks agent schedule info
    picks_action = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='picks-agent',
    ).first()
    agent_info = {}
    if picks_action:
        pdata = picks_action.data or {}
        last_run = pdata.get('last_run', 0)
        interval = pdata.get('interval_hours', 12)
        agent_info = {
            'last_run': last_run,
            'interval_hours': interval,
            'next_run': last_run + interval * 3600,
        }
    # Running status from sysconfig (with stale detection)
    agent_status, agent_launched = _heal_stale_agent(
        'agent_picks-agent_status',
        'agent_picks-agent_launched',
        'picks agent',
    )
    if agent_launched:
        agent_info['launched'] = float(agent_launched)
    agent_info['status'] = agent_status or 'idle'

    # Health/activity/error data from watchdog
    picks_keys = {}
    for sc in SysConfig.objects.filter(key__startswith='agent_picks-agent'):
        picks_keys[sc.key] = sc.value
    last_activity_val = picks_keys.get('agent_picks-agent_last_activity')
    last_error_time_val = picks_keys.get('agent_picks-agent_last_error_time')
    agent_info['last_activity'] = last_activity_val
    agent_info['last_activity_ago'] = fmt_ago(float(last_activity_val)) if last_activity_val else ''
    agent_info['process_alive'] = picks_keys.get('agent_picks-agent_process_alive')
    agent_info['health'] = picks_keys.get('agent_picks-agent_health')
    agent_info['last_error'] = picks_keys.get('agent_picks-agent_last_error')
    agent_info['last_error_time'] = last_error_time_val
    agent_info['last_error_time_ago'] = fmt_ago(float(last_error_time_val)) if last_error_time_val else ''

    # Override latest run duration with agent launched→last_activity
    if sorted_runs and agent_info.get('last_activity') and agent_info.get('launched'):
        try:
            dur = round(float(agent_info['last_activity']) - float(agent_info['launched']))
            if dur > 0:
                sorted_runs[0]['duration_seconds'] = dur
        except (ValueError, TypeError) as e:
            logger.warning("Picks agent duration calc failed: %s", e)

    # Count total configured sources from picks-sources entry
    total_sources = 0
    sources_entry = Entry.objects.filter(
        context__name='picks', deleted_at__isnull=True,
        data__entry_id='picks-sources',
    ).first()
    if sources_entry and sources_entry.content:
        total_sources = sum(1 for line in sources_entry.content.splitlines()
                           if line.strip().startswith('- '))

    return JsonResponse({
        'runs': sorted_runs,
        'agent': agent_info,
        'total_sources': total_sources,
    })


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_picks_update(request):
    """Update a pick's data field (thumbs, kept, archived)."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    entry_id = body.get('entry_id')
    field = body.get('field')
    value = body.get('value')

    if not entry_id or field not in ('thumbs', 'kept', 'archived', 'readme'):
        return JsonResponse({'error': 'entry_id and valid field required'}, status=400)

    entry = Entry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if not entry:
        return JsonResponse({'error': 'Entry not found'}, status=404)

    # Idempotent archive: if user clicks archive on something already in
    # the archive, do nothing — don't bump timestamp_modified, don't create
    # a version snapshot. Archiving the already-archived is a no-op.
    if field == 'archived' and value and entry.status == 'archive':
        return JsonResponse({'ok': True, 'noop': 'already archived'})

    data = entry.data if isinstance(entry.data, dict) else {}
    data[field] = value
    entry.data = data

    if field == 'archived':
        if value:
            entry.status = 'archive'
        elif entry.status == 'archive':
            # Restore from archive: clear the status so the entry reappears
            # in default dashboard views.
            entry.status = None
    if field == 'kept' and value:
        entry.status = None  # kept items should not be archived
    if field == 'readme':
        if value:
            Tag.objects.get_or_create(entry_id=entry_id, tag_name='readme')
        else:
            Tag.objects.filter(entry_id=entry_id, tag_name='readme').delete()

    entry.timestamp_modified = time.time()
    entry.save()
    return JsonResponse({'ok': True})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_picks_archive_run(request):
    """Archive all non-kept picks in a given run."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    run_id = body.get('run_id')
    if not run_id:
        return JsonResponse({'error': 'run_id required'}, status=400)

    entries = Entry.objects.filter(
        kind='bookmark',
        context__name='picks',
        deleted_at__isnull=True,
        data__run=run_id,
    )

    now = time.time()
    count = 0
    for entry in entries:
        data = entry.data if isinstance(entry.data, dict) else {}
        if data.get('kept'):
            continue
        data['archived'] = True
        entry.data = data
        entry.status = 'archive'
        entry.timestamp_modified = now
        entry.save()
        count += 1

    return JsonResponse({'ok': True, 'archived': count})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_picks_run(request):
    """Trigger an immediate picks run by clearing last_run and waking action agent."""
    picks_action = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='picks-agent',
    ).first()
    if not picks_action:
        logger.error("api_picks_run: picks-agent action entry not found")
        return JsonResponse({'error': 'picks-agent action not found'}, status=404)

    # Check if already running
    status = SysConfig.objects.filter(
        key='agent_picks-agent_status'
    ).values_list('value', flat=True).first()
    if status == 'running':
        logger.warning("api_picks_run: picks agent already running, rejecting")
        return JsonResponse({'error': 'Picks agent already running'}, status=409)

    # Clear last_run so get_due_actions sees it as overdue
    data = picks_action.data or {}
    data['last_run'] = 0
    picks_action.data = data
    picks_action.timestamp_modified = time.time()
    picks_action.save(update_fields=['data', 'timestamp_modified'])

    # Wake the action agent via SIGHUP
    wake_ok, wake_msg = _wake_action_agent()
    if not wake_ok:
        return JsonResponse({'ok': True, 'warning': wake_msg})

    logger.info("api_picks_run: triggered, action agent woken")
    return JsonResponse({'ok': True})


@login_required
def readme_page(request):
    """Render the ReadMe reading list page."""
    return render(request, 'tjai_app/readme.html')


@login_required
def api_readme_data(request):
    """Return all entries tagged :readme, reverse chronological."""
    readme_tag_ids = Tag.objects.filter(tag_name='readme').values_list('entry_id', flat=True)
    entries = Entry.objects.filter(
        id__in=readme_tag_ids,
        deleted_at__isnull=True,
    ).order_by('-timestamp_modified')

    entry_ids = [e.id for e in entries]
    tags_by_entry = {}
    for t in Tag.objects.filter(entry_id__in=entry_ids):
        tags_by_entry.setdefault(t.entry_id, []).append(t.tag_name)

    items = []
    for e in entries:
        data = e.data if isinstance(e.data, dict) else {}
        content = e.content or ''
        title = content
        url = ''
        if content.startswith('[') and '](' in content:
            title = content[1:content.index('](')]
            url = content[content.index('](') + 2:].rstrip(')')
        other_tags = [t for t in tags_by_entry.get(e.id, []) if t != 'readme']
        items.append({
            'id': e.id,
            'title': title,
            'url': url,
            'source': data.get('source', ''),
            'precis': data.get('precis', ''),
            'context': e.context_id,
            'kind': e.kind,
            'modified': e.timestamp_modified,
            'modified_display': fmt_datetime(e.timestamp_modified),
            'tags': other_tags,
        })

    return JsonResponse({'items': items})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_readme_dismiss(request):
    """Remove :readme tag from an entry (marks it as read)."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    entry_id = body.get('entry_id')
    if not entry_id:
        return JsonResponse({'error': 'entry_id required'}, status=400)

    deleted = Tag.objects.filter(entry_id=entry_id, tag_name='readme').delete()
    if deleted[0] == 0:
        return JsonResponse({'error': 'Tag not found'}, status=404)

    return JsonResponse({'ok': True})


@login_required
def system_health(request):
    """Render the system health page."""
    return render(request, 'tjai_app/system_health.html')


GUNICORN_CONTROL_SOCKET = "/run/tjai/gunicorn.ctl"
GUNICORN_PYSPY_DIR = "/var/log/tjai/pyspy"


def _proc_stat(pid):
    try:
        raw = open(f"/proc/{pid}/stat", encoding="utf-8").read()
        fields = raw.rsplit(") ", 1)[1].split()
        return {
            "state": fields[0],
            "utime": int(fields[11]),
            "stime": int(fields[12]),
            "starttime": int(fields[19]),
        }
    except (OSError, IndexError, ValueError):
        return None


def _proc_status(pid):
    status = {}
    try:
        for line in open(f"/proc/{pid}/status", encoding="utf-8"):
            key, _, val = line.partition(":")
            status[key] = val.strip()
    except OSError:
        return {}
    return status


def _proc_metrics(pid):
    stat = _proc_stat(pid)
    status = _proc_status(pid)
    if not stat and not status:
        return {"alive": False}

    metrics = {"alive": True}
    if stat:
        ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        cpu_seconds = (stat["utime"] + stat["stime"]) / ticks
        metrics["state"] = stat["state"]
        metrics["cpu_seconds"] = round(cpu_seconds, 2)
        try:
            uptime_seconds = float(open("/proc/uptime", encoding="utf-8").read().split()[0])
            age_seconds = max(0.1, uptime_seconds - (stat["starttime"] / ticks))
            metrics["age_seconds"] = round(age_seconds)
            metrics["cpu_percent_lifetime"] = round(cpu_seconds / age_seconds * 100, 2)
        except (OSError, ValueError, ZeroDivisionError):
            pass

    vmrss = status.get("VmRSS", "")
    if vmrss.endswith(" kB"):
        try:
            metrics["rss_mb"] = round(int(vmrss[:-3].strip()) / 1024, 1)
        except ValueError:
            pass
    threads = status.get("Threads")
    if threads:
        try:
            metrics["threads"] = int(threads)
        except ValueError:
            pass
    return metrics


def _recent_pyspy_dumps(limit=5):
    try:
        entries = []
        with os.scandir(GUNICORN_PYSPY_DIR) as it:
            for entry in it:
                if not entry.is_file() or not entry.name.endswith(".txt"):
                    continue
                st = entry.stat()
                entries.append({
                    "path": entry.path,
                    "name": entry.name,
                    "size_bytes": st.st_size,
                    "modified": st.st_mtime,
                    "modified_ago": fmt_ago(st.st_mtime),
                })
        return sorted(entries, key=lambda x: x["modified"], reverse=True)[:limit]
    except OSError:
        return []


def _gunicorn_command(client, command):
    try:
        return client.send_command(command), None
    except Exception as e:
        return None, str(e)


def _collect_gunicorn_health():
    health = {
        "socket": GUNICORN_CONTROL_SOCKET,
        "socket_exists": os.path.exists(GUNICORN_CONTROL_SOCKET),
        "status": "unknown",
        "errors": [],
        "warnings": [],
        "workers": [],
        "pyspy_dumps": _recent_pyspy_dumps(),
    }

    if not health["socket_exists"]:
        health["status"] = "red"
        health["errors"].append("gunicorn control socket is missing")
        return health

    try:
        from gunicorn.ctl.client import ControlClient
    except Exception as e:
        health["status"] = "red"
        health["errors"].append(f"gunicorn control client unavailable: {e}")
        return health

    try:
        with ControlClient(GUNICORN_CONTROL_SOCKET, timeout=2.0) as client:
            for key, command in (
                ("all", "show all"),
                ("stats", "show stats"),
                ("config", "show config"),
                ("listeners", "show listeners"),
            ):
                result, error = _gunicorn_command(client, command)
                if error:
                    health["errors"].append(f"{command}: {error}")
                else:
                    health[key] = result
    except Exception as e:
        health["status"] = "red"
        health["errors"].append(f"control socket connection failed: {e}")
        return health

    all_info = health.get("all") or {}
    stats = health.get("stats") or {}
    config = health.get("config") or {}
    health["arbiter"] = all_info.get("arbiter") or {}
    arbiter_pid = health["arbiter"].get("pid") or stats.get("pid")
    if arbiter_pid:
        health["arbiter"].update(_proc_metrics(arbiter_pid))

    workers = all_info.get("web_workers") or []
    enriched_workers = []
    hot_workers = 0
    stale_workers = 0
    for worker in workers:
        pid = worker.get("pid")
        item = dict(worker)
        if pid:
            item.update(_proc_metrics(pid))
            if item.get("cpu_percent_lifetime", 0) >= 80:
                hot_workers += 1
        heartbeat = worker.get("last_heartbeat")
        if heartbeat is not None and heartbeat > 60:
            stale_workers += 1
        enriched_workers.append(item)
    health["workers"] = enriched_workers

    target = stats.get("workers_target") or config.get("workers")
    current = stats.get("workers_current") or len(enriched_workers)
    health["workers_current"] = current
    health["workers_target"] = target
    health["worker_class"] = config.get("worker_class")
    health["threads"] = config.get("threads")
    health["timeout"] = config.get("timeout")
    health["reloads"] = stats.get("reloads")
    health["uptime"] = stats.get("uptime")

    if target and current != target:
        health["warnings"].append(f"worker count {current} != target {target}")
    if stale_workers:
        health["warnings"].append(f"{stale_workers} worker heartbeat(s) stale")
    if hot_workers:
        health["warnings"].append(f"{hot_workers} worker(s) lifetime CPU >= 80%")

    if health["errors"]:
        health["status"] = "red"
    elif health["warnings"]:
        health["status"] = "yellow"
    else:
        health["status"] = "green"

    return health


@login_required
def api_system_status(request):
    """Return health status + agent status — lightweight poll for menu colors."""
    health_row = SysConfig.objects.filter(
        key='system_health_status'
    ).values_list('value', 'timestamp_modified').first()
    status = health_row[0] if health_row else ''
    health_age = time.time() - health_row[1] if health_row else float('inf')
    # Health collection runs every 30 min; if >1h stale, agent is down
    HEALTH_STALE = 3600
    if health_age > HEALTH_STALE:
        status = 'red'

    # Agent health: check heartbeat staleness and overdue actions
    agents_status = 'green'
    sc_vals = dict(SysConfig.objects.filter(
        key__in=['action_agent_heartbeat', 'action_agent_started']
    ).values_list('key', 'value'))
    hb = sc_vals.get('action_agent_heartbeat', '')
    started = sc_vals.get('action_agent_started', '')
    now = time.time()
    # Heartbeat stale > 5 min means agent is stuck or down
    HEARTBEAT_STALE = 300
    if hb:
        try:
            if now - float(hb) > HEARTBEAT_STALE:
                agents_status = 'red'
        except (ValueError, TypeError):
            pass
    elif started:
        # No heartbeat yet but agent started — check if started too long ago
        try:
            if now - float(started) > HEARTBEAT_STALE:
                agents_status = 'red'
        except (ValueError, TypeError):
            pass

    # Check for recent agent failures (ERROR logs from agent_complete in last hour)
    if agents_status == 'green':
        from django.utils import timezone as _tz
        recent_errors = AppLog.objects.filter(
            source='agent_complete',
            level__gte=40,
            timestamp__gte=_tz.now() - timedelta(hours=24),
        ).exists()
        if recent_errors:
            agents_status = 'red'

    # Check for overdue periodic actions (only if heartbeat is ok)
    if agents_status == 'green':
        from .action_runner import get_next_scheduled_time
        overdue_actions = Entry.objects.filter(
            kind='action', deleted_at__isnull=True,
        ).exclude(status='done').exclude(status='blocked')
        OVERDUE_THRESHOLD = 600  # 10 min grace before flagging
        for action in overdue_actions:
            data = action.data or {}
            if data.get('trigger') != 'periodic':
                continue
            next_due = get_next_scheduled_time(action)
            if next_due + OVERDUE_THRESHOLD < now:
                agents_status = 'red'
                break

    return JsonResponse({'status': status or '', 'agents': agents_status})


def api_system_data(request):
    """Return system health data from sysconfig as JSON.

    Agent status fields are overlaid with live sysconfig values so the
    Actions table reflects current state, not stale cached data.
    """
    raw = SysConfig.objects.filter(
        key='system_health_data'
    ).values_list('value', flat=True).first()
    if not raw:
        return JsonResponse({'error': 'No health data collected yet'}, status=404)

    data = json.loads(raw)

    # Overlay live agent status onto cached actions
    actions = (data.get('tjai') or {}).get('actions', [])
    if actions:
        agent_keys = {sc.key: sc.value
                      for sc in SysConfig.objects.filter(key__startswith='agent_')}
        now = time.time()
        for a in actions:
            aid = a.get('id')
            if not aid:
                continue
            # Find action_id from cached data or look it up
            action_id = a.get('agent_tracking') and None  # need the entry_id
            # Re-derive action_id: it's stored in the action entry's data.entry_id
            # which is already in the cached action as the sysconfig key prefix
            # Try to match by checking if agent_{x}_status exists
            entry = Entry.objects.filter(
                id=aid, deleted_at__isnull=True
            ).values_list('data', flat=True).first()
            if not entry:
                continue
            action_id = (entry or {}).get('entry_id')
            if not action_id:
                continue
            status = agent_keys.get(f'agent_{action_id}_status')
            launched = agent_keys.get(f'agent_{action_id}_launched')
            completed = agent_keys.get(f'agent_{action_id}_completed')
            last_activity = agent_keys.get(f'agent_{action_id}_last_activity')
            tracking = agent_keys.get(f'agent_{action_id}_tracking')

            a['agent_status'] = status
            if tracking:
                a['agent_tracking'] = tracking
            if launched:
                a['agent_launched_min'] = round((now - float(launched)) / 60, 1)
            if completed:
                a['agent_completed_min'] = round((now - float(completed)) / 60, 1)
            if launched and (last_activity or completed):
                try:
                    end = float(last_activity) if last_activity else float(completed)
                    a['agent_duration_min'] = round(
                        (end - float(launched)) / 60, 1)
                except (ValueError, TypeError) as e:
                    logger.warning("Agent duration calc failed for %s: %s",
                                   a.get('content', '?'), e)

    # Overlay live action agent state (restart flag, uptime)
    agents = (data.get('tjai') or {}).get('agents', [])
    for ag in agents:
        if ag.get('name') == 'Action Agent':
            restart_val = SysConfig.objects.filter(
                key='action_agent_restart_requested'
            ).values_list('value', flat=True).first()
            ag['restart_pending'] = bool(restart_val)
            started = SysConfig.objects.filter(
                key='action_agent_started'
            ).values_list('value', flat=True).first()
            if started:
                ag['uptime_min'] = round((time.time() - float(started)) / 60, 1)
            break

    # Include sysconfig dump (redact keys/secrets/tokens)
    _secret_keywords = ('key', 'secret', 'token', 'password')
    sysconfig_rows = list(
        SysConfig.objects.all()
        .order_by('key')
        .values_list('key', 'value', 'timestamp_modified')
    )
    def _sysconfig_row(k, v, m):
        is_secret = any(s in k.lower() for s in _secret_keywords)
        display_val = '***' if is_secret else (v[:200] if v else '')
        row = {'key': k, 'value': display_val, 'modified': m, 'modified_ago': fmt_ago(m)}
        # Detect epoch-valued sysconfig entries and add _ago display
        if not is_secret and v:
            import re as _re
            if _re.match(r'^\d{10}(\.\d+)?$', v):
                try:
                    ts = float(v)
                    if 1700000000 < ts < 2000000000:
                        row['value_ago'] = fmt_ago(ts)
                except (ValueError, TypeError):
                    pass
        return row
    data['sysconfig'] = [
        _sysconfig_row(k, v, m)
        for k, v, m in sysconfig_rows
        if k != 'system_health_data'
    ]

    # Pre-format collection timestamp
    if data.get('timestamp'):
        data['timestamp_ago'] = fmt_ago(data['timestamp'])

    # Watchdog data
    wd_status = SysConfig.objects.filter(
        key='watchdog_status'
    ).values_list('value', flat=True).first()
    wd_last_run = SysConfig.objects.filter(
        key='watchdog_last_run'
    ).values_list('value', flat=True).first()
    wd_results_raw = SysConfig.objects.filter(
        key='watchdog_last_results'
    ).values_list('value', flat=True).first()

    wd_data = {
        'status': wd_status or 'unknown',
        'last_run': float(wd_last_run) if wd_last_run else None,
        'last_run_ago': fmt_ago(float(wd_last_run)) if wd_last_run else None,
        'results': json.loads(wd_results_raw) if wd_results_raw else [],
    }

    # Recent non-OK watchdog logs (24h)
    from django.utils import timezone as wd_tz
    wd_cutoff = wd_tz.now() - timedelta(hours=24)
    wd_logs = list(AppLog.objects.filter(
        source='watchdog',
        timestamp__gte=wd_cutoff,
    ).order_by('-timestamp')[:1000].values_list(
        'timestamp', 'levelname', 'message'
    ))
    app_tz = get_app_tz()
    wd_data['recent_alerts'] = [
        {
            'timestamp': ts.astimezone(app_tz).strftime('%H:%M'),
            'level': lvl,
            'message': msg,
        }
        for ts, lvl, msg in wd_logs
    ]

    # Escalation history (24h)
    esc_logs = list(AppLog.objects.filter(
        source='watchdog_escalation',
        timestamp__gte=wd_cutoff,
    ).order_by('-timestamp')[:20].values_list(
        'timestamp', 'message'
    ))
    wd_data['escalations'] = [
        {
            'timestamp': ts.astimezone(app_tz).strftime('%H:%M'),
            'message': msg,
        }
        for ts, msg in esc_logs
    ]

    data['watchdog'] = wd_data

    gunicorn_data = _collect_gunicorn_health()
    data['gunicorn'] = gunicorn_data
    issues = data.get('issues') or []
    if gunicorn_data.get('status') == 'red':
        issues.extend(gunicorn_data.get('errors', []))
    elif gunicorn_data.get('status') == 'yellow':
        issues.extend(gunicorn_data.get('warnings', []))
    data['issues'] = issues

    # Cron jobs — parse the crontab file
    cron_path = os.path.join(django_settings.BASE_DIR, 'scripts', 'cron', 'crontab')
    cron_jobs = []
    try:
        with open(cron_path, encoding='utf-8') as f:
            comment = ''
            for line in f:
                line = line.strip()
                if line.startswith('#') and not line.startswith('# Install:') and not line.startswith('# Source:') and not line.startswith('# tjai'):
                    comment = line.lstrip('# ').strip()
                elif line and not line.startswith('#') and '=' not in line:
                    parts = line.split(None, 5)
                    if len(parts) >= 6:
                        schedule = ' '.join(parts[:5])
                        cmd = parts[5].split('>>')[0].strip()
                        script = os.path.basename(cmd)
                        cron_jobs.append({
                            'schedule': schedule,
                            'script': script,
                            'description': comment,
                        })
                    comment = ''
    except Exception as e:
        cron_jobs = [{'schedule': '', 'script': 'ERROR', 'description': str(e)}]
    data['cron'] = cron_jobs

    return JsonResponse(data)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_system_refresh(request):
    """Request system health data collection via action agent."""
    now = time.time()
    SysConfig.objects.update_or_create(
        key='system_health_refresh_requested',
        defaults={'value': str(now), 'timestamp_modified': now},
    )
    return JsonResponse({'status': 'requested'})


# --- RSS Reader ---

@login_required
def rss_page(request):
    """Render the RSS reader triage page."""
    return render(request, 'tjai_app/rss.html')


@login_required
def api_rss_data(request):
    """Return unread RSS items grouped by category and source."""
    items = RssItem.objects.filter(read=False).order_by('category', 'source', '-published')

    # Find URLs that already have :readme bookmarks
    readme_entry_ids = Tag.objects.filter(tag_name='readme').values_list('entry_id', flat=True)
    readme_entries = Entry.objects.filter(
        id__in=readme_entry_ids, kind='bookmark', deleted_at__isnull=True,
    )
    readme_urls = set()
    for e in readme_entries:
        content = e.content or ''
        if '](' in content:
            readme_urls.add(content[content.index('](') + 2:].rstrip(')'))
        else:
            readme_urls.add(content.strip())

    # Group by category → source, dedup by title within source
    categories = {}
    seen_titles = {}
    for item in items:
        cat = item.category or 'uncategorized'
        if cat not in categories:
            categories[cat] = {}
        src = item.source
        if src not in categories[cat]:
            categories[cat][src] = []
        title_key = (src, item.title)
        if title_key in seen_titles:
            continue
        seen_titles[title_key] = True
        categories[cat][src].append({
            'guid': item.guid,
            'title': item.title,
            'url': item.url,
            'precis': item.precis,
            'published': item.published.isoformat() if item.published else None,
            'published_display': fmt_datetime(item.published) if item.published else None,
            'fetched': item.fetched.isoformat(),
            'author': (item.data or {}).get('author', ''),
            'readme': item.url in readme_urls,
        })

    result = []
    for cat in sorted(categories.keys()):
        sources = []
        for src in sorted(categories[cat].keys()):
            sources.append({
                'source': src,
                'items': categories[cat][src],
            })
        result.append({
            'category': cat,
            'sources': sources,
        })

    total_unread = RssItem.objects.filter(read=False).count()
    oldest = RssItem.objects.filter(read=False, published__isnull=False).order_by('published').values_list('published', flat=True).first()
    from django.utils import timezone as djtz

    # Include feed fetch errors if any
    fetch_errors = []
    err_row = SysConfig.objects.filter(key='rss_fetch_errors').first()
    if err_row:
        try:
            fetch_errors = json.loads(err_row.value)
        except (json.JSONDecodeError, TypeError):
            pass

    return JsonResponse({
        'categories': result,
        'total_unread': total_unread,
        'oldest_date': oldest.isoformat() if oldest else None,
        'oldest_date_display': fmt_datetime(oldest) if oldest else None,
        'server_time': djtz.now().isoformat(),
        'fetch_errors': fetch_errors,
    })


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_rss_mark_read(request):
    """Mark items from a source as read, only those fetched before a cutoff time."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    source = body.get('source')
    before = body.get('before')
    if not source:
        return JsonResponse({'error': 'source required'}, status=400)

    qs = RssItem.objects.filter(source=source, read=False)
    if before:
        from django.utils.dateparse import parse_datetime
        cutoff = parse_datetime(before)
        if cutoff:
            qs = qs.filter(fetched__lte=cutoff)
    count = qs.update(read=True)
    return JsonResponse({'ok': True, 'marked': count})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_rss_mark_item_read(request):
    """Mark a single RSS item as read by guid."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    guid = body.get('guid')
    if not guid:
        return JsonResponse({'error': 'guid required'}, status=400)

    count = RssItem.objects.filter(guid=guid, read=False).update(read=True)
    total_unread = RssItem.objects.filter(read=False).count()
    return JsonResponse({'ok': True, 'marked': count, 'total_unread': total_unread})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_rss_mark_guids_read(request):
    """Mark multiple RSS items as read by a list of guids."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    guids = body.get('guids')
    if not guids or not isinstance(guids, list):
        return JsonResponse({'error': 'guids list required'}, status=400)

    count = RssItem.objects.filter(guid__in=guids, read=False).update(read=True)
    total_unread = RssItem.objects.filter(read=False).count()
    return JsonResponse({'ok': True, 'marked': count, 'total_unread': total_unread})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_rss_mark_all_read(request):
    """Mark all unread RSS items as read, only those fetched before a cutoff time."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        body = {}

    before = body.get('before')
    qs = RssItem.objects.filter(read=False)
    if before:
        from django.utils.dateparse import parse_datetime
        cutoff = parse_datetime(before)
        if cutoff:
            qs = qs.filter(fetched__lte=cutoff)
    count = qs.update(read=True)
    return JsonResponse({'ok': True, 'marked': count})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_rss_readme(request):
    """Toggle :readme bookmark for an RSS item."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    url = body.get('url', '').strip()
    title = body.get('title', '').strip()
    activate = body.get('activate', True)

    if not url:
        return JsonResponse({'error': 'url required'}, status=400)

    from django.db.models import Q

    if activate:
        # Check for existing bookmark with this URL
        existing = Entry.objects.filter(
            kind='bookmark',
            deleted_at__isnull=True,
        ).filter(
            Q(content__contains=f'({url})') | Q(content=url) | Q(content__startswith=url + ' ')
        ).first()

        if existing:
            # Just ensure :readme tag exists
            Tag.objects.get_or_create(entry_id=existing.id, tag_name='readme')
            return JsonResponse({'ok': True, 'entry_id': str(existing.id)})

        # Create bookmark with :readme tag
        content = f"[{title}]({url})" if title else url
        now = time.time()
        entry = Entry.objects.create(
            id=str(uuid.uuid7()),
            content=content,
            kind='bookmark',
            timestamp_created=now,
            timestamp_modified=now,
            is_dirty=1,
        )
        Tag.objects.create(tag_name='readme', entry=entry)
        return JsonResponse({'ok': True, 'entry_id': str(entry.id)})
    else:
        # Deactivate: remove :readme tag from bookmark with this URL
        matching = Entry.objects.filter(
            kind='bookmark',
            deleted_at__isnull=True,
        ).filter(
            Q(content__contains=f'({url})') | Q(content=url) | Q(content__startswith=url + ' ')
        ).first()
        if matching:
            Tag.objects.filter(entry_id=matching.id, tag_name='readme').delete()
        return JsonResponse({'ok': True})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_rss_fetch(request):
    """Trigger a manual RSS fetch."""
    import subprocess
    script = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scripts', 'fetch_rss.py')
    venv_python = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.venv', 'bin', 'python3')
    result = subprocess.run(
        [venv_python, script],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        return JsonResponse({
            'error': 'Fetch failed',
            'stderr': result.stderr[-500:] if result.stderr else '',
        }, status=500)
    return JsonResponse({'ok': True, 'output': result.stdout[-500:] if result.stdout else ''})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_rss_add_source(request):
    """Validate a URL, find its RSS feed, and add to rss-sources entry."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    url = (body.get('url') or '').strip()
    category = (body.get('category') or 'uncategorized').strip().lower()

    if not url:
        return JsonResponse({'error': 'url required'}, status=400)

    # Try to find the actual RSS feed URL
    import feedparser
    import requests as req_lib

    feed_url = url
    feed = feedparser.parse(url)

    # If the URL isn't a feed, look for <link rel="alternate"> in the HTML
    if feed.bozo and not feed.entries:
        try:
            resp = req_lib.get(url, timeout=15, headers={'User-Agent': 'tjai-rss/1.0'})
            resp.raise_for_status()
            html = resp.text
            # Look for RSS/Atom feed links
            feed_links = re.findall(
                r'<link[^>]+type=["\']application/(?:rss\+xml|atom\+xml)["\'][^>]*href=["\']([^"\']+)["\']',
                html, re.IGNORECASE,
            )
            if not feed_links:
                feed_links = re.findall(
                    r'<link[^>]+href=["\']([^"\']+)["\'][^>]*type=["\']application/(?:rss\+xml|atom\+xml)["\']',
                    html, re.IGNORECASE,
                )
            if feed_links:
                # Resolve relative URLs
                from urllib.parse import urljoin
                feed_url = urljoin(url, feed_links[0])
                feed = feedparser.parse(feed_url)
            else:
                return JsonResponse({'error': f'No RSS feed found at {url}'}, status=400)
        except Exception as e:
            return JsonResponse({'error': f'Failed to fetch {url}: {e}'}, status=400)

    if not feed.entries:
        return JsonResponse({'error': f'Feed at {feed_url} has no entries'}, status=400)

    feed_title = feed.feed.get('title', feed_url) if hasattr(feed, 'feed') else feed_url

    # Add to rss-sources entry
    entry = Entry.objects.filter(
        data__entry_id='rss-sources',
        deleted_at__isnull=True,
    ).first()
    if not entry:
        return JsonResponse({'error': 'rss-sources entry not found'}, status=404)

    content = entry.content or ''
    heading = f'## {category}'

    if heading in content:
        # Add URL under existing category heading
        lines = content.split('\n')
        insert_idx = None
        for i, line in enumerate(lines):
            if line.strip().lower() == heading:
                # Find the end of this category section
                insert_idx = i + 1
                while insert_idx < len(lines):
                    next_line = lines[insert_idx].strip()
                    if next_line.startswith('## ') or (not next_line and insert_idx + 1 < len(lines) and lines[insert_idx + 1].strip().startswith('## ')):
                        break
                    insert_idx += 1
                break
        if insert_idx is not None:
            lines.insert(insert_idx, feed_url)
            content = '\n'.join(lines)
        else:
            content += f'\n{feed_url}'
    else:
        # Add new category section
        content = content.rstrip() + f'\n\n{heading}\n{feed_url}'

    entry.content = content
    entry.timestamp_modified = time.time()
    entry.save()

    return JsonResponse({
        'ok': True,
        'feed_url': feed_url,
        'feed_title': feed_title,
        'category': category,
        'entry_count': len(feed.entries),
    })


# ── Agent Queue Observability ──────────────────────────────────────────

@login_required
def agent_queue(request):
    """Agent queue observability page."""
    return render(request, 'tjai_app/agent_queue.html')


@login_required
def api_agent_queue_data(request):
    """Build unified agent timeline from structured SysConfig + Entry data.

    Each action produces up to two timeline items:
    - An upcoming/running item (what it will do / is doing next)
    - A completed item (its most recent finished run, from SysConfig)
    Click-through to agent-log for full history per action.
    """
    now = time.time()
    timeline = []
    latest_completions = {}  # action_id -> latest completion from SysConfig

    # ── Load all action entries ──
    actions = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
    ).exclude(status='done').exclude(status='blocked')

    # ── Load all sysconfig keys in one query ──
    from django.db.models import Q
    sc_all = {
        sc.key: sc.value
        for sc in SysConfig.objects.filter(
            Q(key__startswith='agent_') | Q(key__startswith='action_agent_')
        )
    }

    # Collect entry UUIDs we'll need titles for (batch-fetch later)
    entry_ids_needed = set()

    # ── Per-action: build upcoming/running + latest completion ──
    action_uuid_map = {}  # action_id -> entry UUID for linking
    action_data_map = {}  # action_id -> action.data dict
    for action in actions:
        data = action.data or {}
        action_id = data.get('entry_id', '')
        if not action_id:
            continue

        action_uuid = action.id
        action_uuid_map[action_id] = action_uuid
        action_data_map[action_id] = data
        prefix = f'agent_{action_id}_'
        from .action_runner import get_next_scheduled_time
        next_due = get_next_scheduled_time(action)
        interval_hours = data.get('interval_hours', 24)
        sc_status = sc_all.get(f'{prefix}status', '')
        label = action.content.split('\n')[0][:80]

        # ── Running ──
        if sc_status in ('running', 'waiting_subagents'):
            launched = sc_all.get(f'{prefix}launched', '')
            entry_uuid = sc_all.get(f'{prefix}entry', '')
            tracking = sc_all.get(f'{prefix}tracking', '')
            health = sc_all.get(f'{prefix}health', '')
            if entry_uuid:
                entry_ids_needed.add(entry_uuid)

            timeline.append({
                'type': 'running',
                'action_id': action_id,
                'action_uuid': action_uuid,
                'content': label,
                'started_at': float(launched) if launched else now,
                'running_sec': round(now - float(launched)) if launched else 0,
                'health': health or sc_status,
                'tracking': tracking,
                'current_entry': entry_uuid,
                '_event_time': now,
            })
        # ── Upcoming (or overdue) ──
        else:
            overdue = next_due <= now
            item = {
                'type': 'upcoming',
                'action_id': action_id,
                'action_uuid': action_uuid,
                'content': label,
                'due_at': next_due,
                'due_in_sec': round(next_due - now),
                'interval_h': interval_hours,
                'trigger': data.get('trigger', ''),
                # Overdue items sort just above "Now"; future items sort by due time
                '_event_time': now + 0.5 if overdue else next_due,
            }
            scheduled_time = data.get('scheduled_time')
            if scheduled_time:
                item['scheduled_time'] = scheduled_time
            timeline.append(item)

        # ── Latest completion (from SysConfig structured fields) ──
        completed_ts = sc_all.get(f'{prefix}completed', '')
        launched_ts = sc_all.get(f'{prefix}launched', '')
        if completed_ts:
            try:
                completed_f = float(completed_ts)
            except (ValueError, TypeError):
                completed_f = None
            if completed_f:
                entry_uuid = sc_all.get(f'{prefix}entry', '')
                tracking = sc_all.get(f'{prefix}tracking', '')
                last_error = sc_all.get(f'{prefix}last_error', '')

                duration_sec = None
                if launched_ts:
                    try:
                        duration_sec = round(completed_f - float(launched_ts))
                    except (ValueError, TypeError):
                        pass

                run_status = sc_all.get(f'{prefix}status', 'completed')
                if run_status in ('running', 'waiting_subagents', 'idle'):
                    run_status = 'completed'

                if entry_uuid:
                    entry_ids_needed.add(entry_uuid)

                latest_completions[action_id] = {
                    'type': 'completed',
                    'action_id': action_id,
                    'action_uuid': action_uuid,
                    'completed_at': completed_f,
                    'duration_sec': duration_sec,
                    'status': run_status,
                    'entry_id': entry_uuid,
                    'tracking': tracking,
                    'error': last_error if last_error else None,
                    'result_url': data.get('result_url', ''),
                    'progress_log': data.get('progress_log', ''),
                    '_event_time': completed_f,
                }

    # ── Build completed history from AppLog (structured extra_data) ──
    # AppLog entries with extra_data.action_id give us per-completion records.
    # Fall back to SysConfig latest_completions for actions without AppLog history.
    from zoneinfo import ZoneInfo
    cutoff = datetime.now(tz=ZoneInfo('UTC')) - timedelta(hours=48)
    completion_logs = AppLog.objects.filter(
        source='agent_complete',
        timestamp__gte=cutoff,
        level__in=[20, 40],  # INFO and ERROR — success and failure summaries
        extra_data__action_id__isnull=False,
        message__contains='exit_code=',
    ).order_by('-timestamp')[:100]

    seen_from_applog = set()  # action_ids that have AppLog history
    seen_tracking = set()  # deduplicate multiple logs for same run
    for log_entry in completion_logs:
        ed = log_entry.extra_data or {}
        aid = ed.get('action_id', '')
        if not aid:
            continue
        tracking = ed.get('tracking', '')
        dedup_key = f'{aid}:{tracking}' if tracking else f'{aid}:{log_entry.id}'
        if dedup_key in seen_tracking:
            continue
        seen_tracking.add(dedup_key)
        seen_from_applog.add(aid)
        entry_uuid = ed.get('entry_id', '')
        completed_at = log_entry.timestamp.timestamp()

        # Duration + status: prefer structured extra_data, fall back to
        # SysConfig for the latest run
        duration_sec = ed.get('duration_sec')
        status = ed.get('run_status', '')
        if not status:
            status = 'completed'

        if duration_sec is None and aid in latest_completions:
            lc = latest_completions[aid]
            if abs(lc['completed_at'] - completed_at) < 60:
                duration_sec = lc.get('duration_sec')

        if entry_uuid:
            entry_ids_needed.add(entry_uuid)

        # Prefer tracking from AppLog extra_data, fall back to SysConfig latest
        tracking = ed.get('tracking', '')
        if not tracking and aid in latest_completions and abs(
                latest_completions[aid]['completed_at'] - completed_at) < 60:
            tracking = latest_completions[aid].get('tracking', '')

        progress_log = action_data_map.get(aid, {}).get('progress_log', '')

        item = {
            'type': 'completed',
            'action_id': aid,
            'action_uuid': action_uuid_map.get(aid, ''),
            'completed_at': completed_at,
            'duration_sec': duration_sec,
            'status': status,
            'entry_id': entry_uuid,
            'tracking': tracking,
            'result_url': action_data_map.get(aid, {}).get('result_url', ''),
            'progress_log': progress_log,
            '_event_time': completed_at,
        }
        model = ed.get('model')
        if model:
            item['model'] = model
        timeline.append(item)

    # Add SysConfig fallback for actions with no AppLog history yet
    for aid, lc in latest_completions.items():
        if aid not in seen_from_applog:
            timeline.append(lc)

    # ── Batch-fetch entry titles and entry_ids ──
    if entry_ids_needed:
        entry_info = {
            str(row['id']): row
            for row in Entry.objects.filter(
                id__in=list(entry_ids_needed)
            ).values('id', 'content', 'data')
        }
        for item in timeline:
            eid = item.get('current_entry') or item.get('entry_id')
            if eid and eid in entry_info:
                info = entry_info[eid]
                topic = info['content'].split('\n')[0][:100]
                # Use data.entry_id for URLs when available, fall back to UUID
                data_eid = (info['data'] or {}).get('entry_id') if isinstance(info['data'], dict) else None
                url_id = data_eid or eid
                if item['type'] == 'running':
                    item['current_topic'] = topic
                    item['current_entry'] = url_id
                elif item['type'] == 'completed':
                    item['message'] = topic
                    item['entry_id'] = url_id

    # ── Sort: event_time descending (future → now → past) ──
    timeline.sort(key=lambda x: x.get('_event_time', 0), reverse=True)

    for item in timeline:
        item.pop('_event_time', None)
        # Add server-formatted display strings for absolute date cases
        if item.get('due_at'):
            item['due_display'] = fmt_datetime(item['due_at'])
        if item.get('completed_at'):
            item['completed_display'] = fmt_datetime(item['completed_at'])
            item['completed_ago'] = fmt_ago(item['completed_at'])
        if item.get('duration_sec') is not None:
            item['duration_display'] = fmt_duration(item['duration_sec'])

    # ── Daemon status ──
    daemon = {
        'pid': sc_all.get('action_agent_pid', ''),
        'restart_pending': bool(
            sc_all.get('action_agent_restart_requested', '')),
    }
    hb = sc_all.get('action_agent_heartbeat', '')
    if hb:
        try:
            daemon['heartbeat_sec_ago'] = round(now - float(hb))
        except (ValueError, TypeError):
            pass
    started = sc_all.get('action_agent_started', '')
    if started:
        try:
            daemon['uptime_sec'] = round(now - float(started))
        except (ValueError, TypeError):
            pass

    return JsonResponse({'timeline': timeline, 'daemon': daemon})


# --- Goals ---

@login_required
def goals_page(request):
    """Render the goals page."""
    return render(request, 'tjai_app/goals.html')


@login_required
def api_goals_data(request):
    """Return goals list with optional filtering."""
    from . import services
    goals = services.get_goals(include_done=True, max_content_length=0)
    if isinstance(goals, dict) and 'error' in goals:
        return JsonResponse(goals, status=400)

    # Also return available contexts for filter dropdown
    contexts = list(Context.objects.order_by('name').values_list('name', flat=True))
    return JsonResponse({'goals': goals, 'contexts': contexts})


@login_required
def api_goals_detail(request):
    """Return a single goal with its relations. Supports UUID and entry_id."""
    from . import services
    goal_id = request.GET.get('id', '')
    if not goal_id:
        return JsonResponse({'error': 'id parameter is required'}, status=400)

    # If shorter than UUID (36 chars), try entry_id first
    entry = None
    if len(goal_id) < 36:
        entry = Entry.objects.filter(
            data__entry_id=goal_id, deleted_at__isnull=True
        ).first()
    if not entry:
        entry = Entry.objects.filter(
            id=goal_id, deleted_at__isnull=True
        ).first()
    if not entry:
        return JsonResponse({'error': f"Entry '{goal_id}' not found"}, status=404)

    result = services.get_goal(str(entry.id), max_content_length=0)
    if isinstance(result, dict) and 'error' in result:
        return JsonResponse(result, status=400)
    return JsonResponse(result)


@login_required
@require_http_methods(["POST"])
def api_goal_create_note(request):
    """Create a note entry for a goal."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    goal_entry_id = body.get('goal_entry_id', '').strip()
    if not goal_entry_id:
        return JsonResponse({'error': 'goal_entry_id is required'}, status=400)

    note_entry_id = f'{goal_entry_id}-note'

    # Check note doesn't already exist
    if Entry.objects.filter(data__entry_id=note_entry_id, deleted_at__isnull=True).exists():
        return JsonResponse({'error': 'Note already exists', 'url': f'/tjai/entry/{note_entry_id}/'})

    # Verify the goal exists
    goal = Entry.objects.filter(
        data__entry_id=goal_entry_id, deleted_at__isnull=True, kind='goal'
    ).first()
    if not goal:
        return JsonResponse({'error': f"Goal '{goal_entry_id}' not found"}, status=404)

    # Create the note entry — seed with goal title
    goal_title = goal.content.split('\n')[0].strip()
    now = time.time()
    note = Entry.objects.create(
        id=str(uuid.uuid7()),
        content=f'{goal_title} notes',
        kind='memory',
        context=goal.context,
        timestamp_created=now,
        timestamp_modified=now,
        data={
            'entry_id': note_entry_id,
            'rel_goal': goal_entry_id,
        },
    )

    return JsonResponse({'ok': True, 'url': f'/tjai/entry/{note_entry_id}/'})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_goals_create(request):
    """Create a new goal entry."""
    from . import services
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    title = body.get('title', '').strip()
    if not title:
        return JsonResponse({'error': 'title is required'}, status=400)
    description = body.get('content', '').strip()
    content = title + '\n' + description if description else title

    result = services.create_goal(
        content=content,
        context=body.get('context'),
        priority=body.get('priority'),
        status=body.get('status'),
        data=body.get('data'),
        create_context=body.get('create_context', False),
    )
    if isinstance(result, dict) and 'error' in result:
        return JsonResponse(result, status=400)
    return JsonResponse(result)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_goals_relate(request):
    """Create a relation between two entries."""
    from . import services
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    result = services.create_relation(
        entry1_id=body.get('entry1_id', ''),
        entry2_id=body.get('entry2_id', ''),
        relation_type=body.get('relation_type', ''),
        data=body.get('data'),
    )
    if isinstance(result, dict) and 'error' in result:
        return JsonResponse(result, status=400)
    return JsonResponse(result)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_goals_unrelate(request):
    """Delete a relation."""
    from . import services
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    relation_id = body.get('relation_id', '')
    if not relation_id:
        return JsonResponse({'error': 'relation_id is required'}, status=400)

    result = services.delete_relation(relation_id)
    if isinstance(result, dict) and 'error' in result:
        return JsonResponse(result, status=400)
    return JsonResponse(result)
