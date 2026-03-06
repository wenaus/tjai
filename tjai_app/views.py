import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_LIST_RE = re.compile(r'[-*+] |\d+\. ')

def _fix_md_list_spacing(text):
    """Insert blank line before list items that follow a non-list, non-blank line.

    AI-generated markdown often omits the required blank line before a list,
    causing the Python markdown library to render bullets as a paragraph blob.
    """
    lines = text.split('\n')
    result = []
    for i, line in enumerate(lines):
        if (i > 0
                and _LIST_RE.match(line.lstrip())
                and lines[i - 1].strip()
                and not _LIST_RE.match(lines[i - 1].lstrip())):
            result.append('')
        result.append(line)
    return '\n'.join(result)

from .tjai_utils import fmt_datetime, fmt_date, fmt_time, fmt_duration, fmt_ago, get_app_tz
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from django.db.models import Count
from django.db.models.functions import Lower
from django.http import Http404
from django.conf import settings as django_settings
from .models import AppLog, Context, Entry, RssItem, Tag, TagStats, SubNote, Machine, SysConfig


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


def oauth_protected_resource(request):
    """
    OAuth 2.0 Protected Resource Metadata (RFC 9728).

    Returns metadata about this protected resource, including
    the authorization server URL for OAuth discovery.
    """
    if not django_settings.AUTH0_DOMAIN:
        return JsonResponse({"error": "OAuth not configured"}, status=503)

    scheme = "https" if request.is_secure() else "http"
    host = request.get_host()
    script_name = django_settings.FORCE_SCRIPT_NAME or ""
    resource = f"{scheme}://{host}{script_name}/mcp"

    metadata = {
        "resource": resource,
        "authorization_servers": [f"https://{django_settings.AUTH0_DOMAIN}/"],
        "scopes_supported": ["openid", "profile", "email"],
        "bearer_methods_supported": ["header"],
    }
    return JsonResponse(metadata)


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


@csrf_exempt
@require_http_methods(["POST"])
def api_command(request):
    """
    Execute a command on the server.

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
    entry = Entry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if not entry:
        return JsonResponse({"error": f"Entry '{entry_id}' not found or already deleted"}, status=404)

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
    return render(request, 'tjai_app/dashboard.html', {
        'is_archive': request.GET.get('status') == 'archive',
        'is_dialog': request.GET.get('context') == 'claude-code',
    })


@login_required
def dashboard_calendar(request):
    """Return calendar data as JSON for dashboard."""
    now = time.time()

    tz = get_app_tz()
    timezone_name = str(tz)

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

            # Only show today's daily synopsis in calendar
            entry_id = data.get('entry_id', '')
            if entry_id.startswith('daily-') and date_key != today_date_str:
                continue
            date_display = event_dt.strftime('%a %b %-d')
            is_allday = entry_id.startswith('daily-') or not (event_dt.hour or event_dt.minute)
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
    start_dt = monday_of_prev_week.replace(hour=0, minute=0, second=0, microsecond=0)
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
    filter_machine = request.GET.get('machine')
    filter_status = request.GET.get('status')
    filter_date = request.GET.get('date')  # YYYY-MM-DD, filter entries to this day
    filter_from_time = request.GET.get('from_time')  # ISO datetime e.g. 2026-03-04T03:30
    filter_to_time = request.GET.get('to_time')  # ISO datetime e.g. 2026-03-04T05:30
    expand_dialog = request.GET.get('expand_dialog') == '1'
    exclude_contexts = [c for c in request.GET.get('exclude_context', '').split(',') if c]

    # Exclude claude-code (dialog) from default dashboard view
    if not filter_context and 'claude-code' not in exclude_contexts:
        exclude_contexts.append('claude-code')

    base_qs = Entry.objects.filter(
        deleted_at__isnull=True,
    )
    if filter_status:
        base_qs = base_qs.filter(status=filter_status)
    else:
        base_qs = base_qs.exclude(status='archive')

    if filter_kind:
        base_qs = base_qs.filter(kind=filter_kind)
    if filter_context:
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

    # Daily counts for dialog mode — DB query covering last 14 days
    daily_counts = None
    if filter_context and offset == 0:
        tz = get_app_tz()
        now_local = datetime.now(tz=tz)
        day_counts = {}
        for days_ago in range(14):
            day = (now_local - timedelta(days=days_ago)).date()
            day_start = datetime.combine(day, datetime.min.time()).replace(tzinfo=tz)
            day_end = day_start + timedelta(days=1)
            count = base_qs.filter(
                timestamp_modified__gte=day_start.timestamp(),
                timestamp_modified__lt=day_end.timestamp(),
            ).count()
            if count > 0:
                day_counts[day.isoformat()] = count
        daily_counts = [{'date': k, 'count': v} for k, v in sorted(day_counts.items(), reverse=True)]

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

    base_qs = base_qs.order_by('-timestamp_modified')

    recent = base_qs[offset:offset + DASHBOARD_PAGE]

    # Batch fetch tags for all entries
    entry_ids = [e.id for e in recent]
    tags_by_entry = {}
    for t in Tag.objects.filter(entry_id__in=entry_ids):
        tags_by_entry.setdefault(t.entry_id, []).append(t.tag_name)

    recent_entries = []
    app_tz = get_app_tz()
    for e in recent:
        lines = e.content.split('\n')
        data = e.data if isinstance(e.data, dict) else None
        # Use stored line count (accurate for truncated display), fall back to computing
        line_count = (data.get('content_lines') if data else None) or len([l for l in lines if l.strip()])
        # Get tags not already in content
        entry_tags = tags_by_entry.get(e.id, [])
        missing_tags = [t for t in entry_tags if f':{t}' not in e.content]
        event_date_epoch = data.get('event_date') if data else None
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
            'tags': missing_tags,
            'all_tags': entry_tags,
        })

    has_more = len(recent_entries) == DASHBOARD_PAGE
    total_count = base_qs.count() if has_more else offset + len(recent_entries)

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

    # Rebuild tag stats on full page load to purge orphans
    from .tag_stats import rebuild_tag_stats
    rebuild_tag_stats()

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

    # Machine sync status - filter out test machines, find longest since sync
    test_names = {'test', 'test123', 'testhost', 'test-host', 'fake-mac', 'debug'}
    machines_qs = Machine.objects.filter(is_active=1).exclude(hostname__in=test_names)
    machines = []
    oldest_sync = now
    oldest_machine = None
    for m in machines_qs:
        name = m.hostname or m.machine_id[:8]
        if name.lower() in test_names:
            continue
        machines.append(name)
        if m.last_sync and m.last_sync < oldest_sync:
            oldest_sync = m.last_sync
            oldest_machine = name
    sync_age_min = int((now - oldest_sync) / 60) if oldest_machine else 0

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
        'open_todos': open_todos,
        'machines': machines,
        'oldest_sync': {'machine': oldest_machine, 'age_min': sync_age_min} if oldest_machine else None,
        'daily_counts': daily_counts,
    })


@login_required
def dashboard_search(request):
    """Search entries and return JSON in same format as dashboard_status entries."""
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'entries': []})

    qs = Entry.objects.filter(
        content__icontains=q,
        deleted_at__isnull=True,
    )

    # Apply same filters as dashboard_status
    filter_status = request.GET.get('status')
    if filter_status:
        qs = qs.filter(status=filter_status)
    else:
        qs = qs.exclude(status='archive')

    filter_kind = request.GET.get('kind')
    if filter_kind:
        qs = qs.filter(kind=filter_kind)

    filter_context = request.GET.get('context', '').strip()
    if filter_context:
        qs = qs.filter(context_id=filter_context)

    exclude_contexts = [c for c in request.GET.get('exclude_context', '').split(',') if c]
    if exclude_contexts:
        qs = qs.exclude(context_id__in=exclude_contexts)

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

    entries = qs.order_by('-timestamp_modified')[:200]

    # Batch fetch tags
    entry_ids = [e.id for e in entries]
    tags_by_entry = {}
    for t in Tag.objects.filter(entry_id__in=entry_ids):
        tags_by_entry.setdefault(t.entry_id, []).append(t.tag_name)

    result = []
    for e in entries:
        lines = e.content.split('\n')
        data = e.data if isinstance(e.data, dict) else None
        stored = data.get('content_lines') if data else None
        line_count = stored if stored else len([l for l in lines if l.strip()])
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
            'tags': missing_tags,
            'all_tags': entry_tags,
        })

    return JsonResponse({'entries': result})


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
            'line_count': len([l for l in lines if l.strip()]),
        })

    return JsonResponse({'entries': result})


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
    import markdown

    entry_id = request.GET.get('entry_id')
    if not entry_id:
        return JsonResponse({'error': 'entry_id parameter required'}, status=400)

    entry = Entry.objects.filter(
        data__entry_id=entry_id,
        deleted_at__isnull=True,
    ).first()
    if not entry:
        return JsonResponse({'error': 'Synopsis not found'}, status=404)

    content_html = markdown.markdown(
        _fix_md_list_spacing(entry.content),
        extensions=['nl2br', 'tables', 'fenced_code'],
        tab_length=2,
    )
    content_html = re.sub(
        r'(?<!["\'>])(https?://[^\s<]+)',
        r'<a href="\1" target="_blank">\1</a>',
        content_html,
    )

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


@login_required
def agent_log(request):
    """Agent log page — shows recent log entries from the AppLog table."""
    return render(request, 'tjai_app/agent_log.html')


@login_required
def agent_log_data(request):
    """Return agent log entries as JSON."""
    import logging as _logging
    limit = min(int(request.GET.get('limit', 200)), 1000)
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
    entries = [{
        'timestamp': log.timestamp.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S'),
        'level': log.levelname,
        'message': log.message,
        'source': log.source,
    } for log in qs]

    return JsonResponse({'entries': entries})


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
    import markdown
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
    tags = list(Tag.objects.filter(entry_id=entry.id).values_list('tag_name', flat=True))
    lines = [l for l in entry.content.split('\n') if l.strip()]
    data = entry.data if isinstance(entry.data, dict) else None
    # Body content excludes first line (shown in summary header)
    body_lines = entry.content.split('\n')
    body_text = '\n'.join(body_lines[1:]).strip() if len(body_lines) > 1 else ''
    # Determine content format: explicit data.format overrides auto-detection
    fmt = (data.get('format') if data else None)
    if not fmt:
        if entry.context_id == 'recipe':
            fmt = 'txt'
        else:
            fmt = 'md'
    md_exts = ['nl2br', 'tables', 'fenced_code'] if fmt == 'txt' else ['tables', 'fenced_code']
    content_html = markdown.markdown(_fix_md_list_spacing(body_text), extensions=md_exts, tab_length=2) if body_text else ''
    # Linkify bare URLs not already in anchor tags
    content_html = re.sub(
        r'(?<!["\'>])(https?://[^\s<]+)',
        r'<a href="\1">\1</a>',
        content_html
    )
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

    # Relations and tagged entries for goal entries
    relations_json = '[]'
    tagged_entries_json = '[]'
    goal_note_url = None
    goal_note_exists = False
    if entry.kind == 'goal':
        from . import services
        relations = services._get_relations_for_entry(str(entry.id), max_content_length=0)
        relations_json = json.dumps(relations)
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

    return render(request, 'tjai_app/entry_detail.html', {
        'entry': entry,
        'content_html': content_html,
        'tags': tags,
        'line_count': len(lines),
        'first_line': first_line,
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
    })


@login_required
@require_http_methods(["POST"])
def api_entry_save(request, entry_id):
    """Save entry content from the inline editor."""
    entry = Entry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if not entry:
        return JsonResponse({'error': 'Entry not found'}, status=404)
    try:
        data = json.loads(request.body)
        content = data.get('content', '')
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
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
        desired_tags = set(t.strip().lstrip(':') for t in raw_tags.split(',') if t.strip())
    else:
        tag_pattern = re.compile(r'(?:^|\s):([a-zA-Z][a-zA-Z0-9_-]*)')
        desired_tags = set(tag_pattern.findall(content))
    existing_tags = set(Tag.objects.filter(entry_id=entry.id).values_list('tag_name', flat=True))
    for tag_name in desired_tags - existing_tags:
        Tag.objects.create(tag_name=tag_name, entry_id=entry.id)
    for tag_name in existing_tags - desired_tags:
        Tag.objects.filter(entry_id=entry.id, tag_name=tag_name).delete()
    # Content format: md/txt/None (auto)
    fmt_changed = False
    if 'format' in data:
        fmt_val = data['format'] if data['format'] in ('md', 'txt') else None
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
    # Preserve mod time if only tags/context changed (content and other fields unchanged)
    metadata_only = (content == old_content and
                     'name' not in data and not fmt_changed)
    if not metadata_only:
        entry.timestamp_modified = time.time()
    entry.save()
    data_dict = entry.data if isinstance(entry.data, dict) else None
    if data_dict and data_dict.get('entry_id'):
        url = f'/tjai/entry/?entry_id={data_dict["entry_id"]}'
    elif data_dict and data_dict.get('nickname'):
        url = f'/tjai/entry/?nickname={data_dict["nickname"]}'
    elif entry.name:
        url = f'/tjai/entry/?name={entry.name}'
    else:
        url = f'/tjai/entry/?uuid={entry.id}'
    return JsonResponse({'ok': True, 'url': url})


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
        stored = data.get('content_lines') if data else None
        e.line_count = stored if stored else (len(lines) if len(lines) > 1 else None)
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

    if not url:
        return JsonResponse({"error": "url is required"}, status=400)

    content = f"[{title}]({url})" if title else url
    if text:
        content += '   ' + text

    from django.db.models import Q
    duplicate = Entry.objects.filter(
        kind='bookmark',
        deleted_at__isnull=True,
    ).filter(
        Q(content__contains=f'({url})') | Q(content=url) | Q(content__startswith=url + ' ')
    ).first()
    if duplicate:
        if text:
            base = f"[{title}]({url})" if title else url
            duplicate.content = base + '   ' + text
            duplicate.timestamp_modified = time.time()
            duplicate.is_dirty = 1
            duplicate.save(update_fields=['content', 'timestamp_modified', 'is_dirty'])
        return JsonResponse({
            "status": "duplicate",
            "entry_id": duplicate.id,
            "content": duplicate.content,
            "updated": bool(text),
        })

    now = time.time()
    entry = Entry.objects.create(
        id=str(uuid.uuid4()),
        content=content,
        kind='bookmark',
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )
    Tag.objects.create(tag_name='chrome', entry=entry)

    from .tagger import tag_bookmark
    auto_tags = tag_bookmark(entry)

    return JsonResponse({
        "status": "ok",
        "entry_id": entry.id,
        "content": content,
        "auto_tags": auto_tags,
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_add_journal(request):
    """Create a journal entry from an external source (Gmail Add-on, Chrome extension).

    Requires Bearer token matching SysConfig 'gmail_addon_api_key'.

    Request body: {title, event_timestamp, location, zoom_url, gmail_url,
                   indico_url, source (default: "gmail")}
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
    content = " ".join(parts)

    now = time.time()
    entry = Entry.objects.create(
        id=str(uuid.uuid4()),
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
def api_add_entry(request):
    """Create a generic entry from an external source (Gmail addon).

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

    if not content:
        return JsonResponse({"error": "content is required"}, status=400)

    context_obj = None
    if context_name:
        context_obj = Context.objects.filter(name=context_name).first()

    now = time.time()
    entry = Entry.objects.create(
        id=str(uuid.uuid4()),
        content=content,
        kind=kind,
        context=context_obj,
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
def api_dialog(request):
    """Record and retrieve Claude Code dialog turns.

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
            tag_name='ccdialog'
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

    entry = Entry.objects.create(
        id=str(uuid.uuid4()),
        content=content,
        kind='memory',
        context_id='claude-code',
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=0,
        data=entry_data,
    )
    Tag.objects.create(tag_name='ccdialog', entry=entry)
    for tag in extra_tags:
        Tag.objects.create(tag_name=tag, entry=entry)

    return JsonResponse({"status": "ok", "entry_id": entry.id})


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
    ).exclude(status='archive').order_by('-timestamp_modified')[:200]

    entry_ids = [e.id for e in entries]
    tags_by_entry = {}
    for t in Tag.objects.filter(entry_id__in=entry_ids):
        tags_by_entry.setdefault(t.entry_id, []).append(t.tag_name)

    result = []
    for e in entries:
        lines = e.content.split('\n')
        data = e.data if isinstance(e.data, dict) else None
        stored = data.get('content_lines') if data else None
        line_count = stored if stored else len([l for l in lines if l.strip()])
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

    return JsonResponse({
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


@login_required
def picks(request):
    """Render the picks triage page."""
    return render(request, 'tjai_app/picks.html')


# --- Research ---

@login_required
def research_page(request):
    """Render the research queue page."""
    return render(request, 'tjai_app/research.html')


@login_required
def api_research_data(request):
    """Return research queue entries and agent status as JSON."""
    research_ids = Tag.objects.filter(
        tag_name='research_topic'
    ).values_list('entry_id', flat=True)
    entries = Entry.objects.filter(
        id__in=research_ids,
        kind='memory',
        deleted_at__isnull=True,
    ).order_by('-timestamp_modified')

    items = []
    for e in entries:
        data = e.data if isinstance(e.data, dict) else {}
        items.append({
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
        })

    # System prompt entry UUID
    sysprompt = Entry.objects.filter(
        data__entry_id='research-system-prompt',
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

    return JsonResponse({
        'items': items,
        'sysprompt_id': str(sysprompt) if sysprompt else None,
        'agent': agent_status,
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

    # Find research-agent action entry
    research_action = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='research-agent',
    ).first()
    if not research_action:
        logger.error("api_research_run: research-agent action entry not found in DB")
        return JsonResponse({'error': 'research-agent action not found'}, status=404)

    # Check if already running
    status = SysConfig.objects.filter(
        key='agent_research-agent_status'
    ).values_list('value', flat=True).first()
    if status == 'running':
        logger.warning("api_research_run: research agent already running, rejecting")
        return JsonResponse({'error': 'Research agent already running'}, status=409)

    data = research_action.data or {}

    if entry_id != 'all':
        # Specific item: look up by data.entry_id (human-readable identifier)
        target = Entry.objects.filter(
            data__entry_id=entry_id, deleted_at__isnull=True
        ).first()
        if not target:
            logger.error("api_research_run: no entry with data.entry_id=%r", entry_id)
            return JsonResponse({'error': f'Research entry not found: {entry_id}'}, status=404)
        data['next_target'] = (
            f"SPECIFIC TARGET:\nEntry UUID: {target.id}\n"
            f"Topic: {target.content}"
        )
        data['next_target_entry_id'] = str(target.id)
    else:
        # Submit All: find the first pending item and use SPECIFIC TARGET
        research_ids = Tag.objects.filter(
            tag_name='research_topic'
        ).values_list('entry_id', flat=True)
        first_item = Entry.objects.filter(
            id__in=research_ids,
            kind='memory',
            deleted_at__isnull=True,
        ).exclude(status='done').order_by('priority', 'timestamp_created').first()
        if not first_item:
            return JsonResponse({'error': 'No pending research items'}, status=400)
        data['next_target'] = (
            f"SPECIFIC TARGET:\nEntry UUID: {first_item.id}\n"
            f"Topic: {first_item.content}"
        )
        data['next_target_entry_id'] = str(first_item.id)

    target_uuid = data.get('next_target_entry_id')
    topic_line = data.get('next_target', '').split('\n')[-1]  # "Topic: ..."

    # Record start time on the target entry for duration tracking
    if target_uuid:
        target_entry = Entry.objects.filter(id=target_uuid).first()
        if target_entry:
            tdata = target_entry.data or {}
            tdata['started_at'] = time.time()
            target_entry.data = tdata
            target_entry.save(update_fields=['data'])

    # Single item: prevent queue drain from chaining to the next item
    now = time.time()
    if entry_id != 'all':
        SysConfig.objects.update_or_create(
            key='research_stop_requested',
            defaults={'value': '1', 'timestamp_modified': now})
    else:
        # Submit All: clear any previous stop request
        SysConfig.objects.update_or_create(
            key='research_stop_requested',
            defaults={'value': '', 'timestamp_modified': now})

    # Force-run: set scheduled_time to now so scheduler sees it as due
    # (just setting last_run=0 fails when scheduled_time is in the future today)
    original_scheduled = data.get('scheduled_time')
    if original_scheduled:
        data['scheduled_time_config'] = original_scheduled
        tz = get_app_tz()
        data['scheduled_time'] = datetime.now(tz).strftime('%H%M')
    data['last_run'] = 0
    research_action.data = data
    research_action.timestamp_modified = now
    research_action.save(update_fields=['data', 'timestamp_modified'])

    _log_research(logging.INFO,
                  f"Submit triggered — {topic_line}",
                  entry_id=target_uuid)

    # Wake the action agent via SIGHUP
    wake_ok, wake_msg = _wake_action_agent()
    if not wake_ok:
        _log_research(logging.WARNING,
                      f"Action agent wake failed: {wake_msg}",
                      entry_id=target_uuid)
        return JsonResponse({'ok': True, 'warning': wake_msg})

    _log_research(logging.INFO, "Action agent woken", entry_id=target_uuid)

    # Dispatch Gemini and ChatGPT in parallel (single-item only)
    if entry_id != 'all' and target_uuid:
        target_entry = Entry.objects.filter(id=target_uuid).first()
        if target_entry:
            tdata = target_entry.data or {}
            base_entry_id = tdata.get('entry_id')
            topic_text = target_entry.content.split('\n')[0].strip()
            if base_entry_id and topic_text:
                from .action_runner import dispatch_multimodel
                dispatch_multimodel(
                    topic_text=topic_text,
                    base_entry_id=base_entry_id,
                    base_uuid=str(target_entry.id),
                    context_obj=target_entry.context,
                )

    return JsonResponse({'ok': True, 'entry_id': entry_id})


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_research_stop(request):
    """Soft stop: finish current item, then don't continue to next."""
    status = SysConfig.objects.filter(
        key='agent_research-agent_status'
    ).values_list('value', flat=True).first()
    if status != 'running':
        return JsonResponse({'error': 'Research agent not running'}, status=409)

    now = time.time()
    SysConfig.objects.update_or_create(
        key='research_stop_requested',
        defaults={'value': '1', 'timestamp_modified': now},
    )
    return JsonResponse({'ok': True})


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
        id=str(uuid.uuid4()),
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

    # Auto-submit: trigger research on the new entry immediately
    research_action = Entry.objects.filter(
        kind='action', deleted_at__isnull=True,
        data__entry_id='research-agent',
    ).first()
    auto_submitted = False
    if research_action:
        status = SysConfig.objects.filter(
            key='agent_research-agent_status'
        ).values_list('value', flat=True).first()
        if status != 'running':
            rdata = research_action.data or {}
            # Force-run: set scheduled_time to now so scheduler sees it as due
            original_scheduled = rdata.get('scheduled_time')
            if original_scheduled:
                rdata['scheduled_time_config'] = original_scheduled
                tz = get_app_tz()
                rdata['scheduled_time'] = datetime.now(tz).strftime('%H%M')
            rdata['last_run'] = 0
            rdata['next_target'] = (
                f"SPECIFIC TARGET:\nEntry UUID: {new_entry.id}\n"
                f"Topic: {new_entry.content}"
            )
            rdata['next_target_entry_id'] = str(new_entry.id)
            research_action.data = rdata
            research_action.timestamp_modified = now
            research_action.save(update_fields=['data', 'timestamp_modified'])

            # Record start time on the new entry
            edata = new_entry.data or {}
            edata['started_at'] = now
            new_entry.data = edata
            new_entry.save(update_fields=['data'])

            # Single item: prevent queue drain chaining
            SysConfig.objects.update_or_create(
                key='research_stop_requested',
                defaults={'value': '1', 'timestamp_modified': now})

            _wake_action_agent()

            # Dispatch Gemini and ChatGPT in parallel
            from .action_runner import dispatch_multimodel
            dispatch_multimodel(
                topic_text=topic,
                base_entry_id=new_entry_id,
                base_uuid=str(new_entry.id),
                context_obj=context_obj,
            )

            _log_research(logging.INFO,
                          f"Rerun auto-submitted: {new_entry_id}",
                          entry_id=str(new_entry.id))
            auto_submitted = True

    return JsonResponse({
        'ok': True,
        'new_entry_id': new_entry_id,
        'new_uuid': str(new_entry.id),
        'version': next_ver,
        'auto_submitted': auto_submitted,
    })


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
    if not topic_uuid:
        return JsonResponse({'error': 'uuid required'}, status=400)

    # Get the parent research entry for display info
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
            'content': e.content[:500] if e.content else '',
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

    data = entry.data if isinstance(entry.data, dict) else {}
    data[field] = value
    entry.data = data

    if field == 'archived' and value:
        entry.status = 'archive'
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
            id=str(uuid.uuid4()),
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
    for action in actions:
        data = action.data or {}
        action_id = data.get('entry_id', '')
        if not action_id:
            continue

        action_uuid = action.id
        action_uuid_map[action_id] = action_uuid
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
        level=20,  # INFO only — the exit_code summary line
        extra_data__action_id__isnull=False,
        message__contains='exit_code=',
    ).order_by('-timestamp')[:100]

    seen_from_applog = set()  # action_ids that have AppLog history
    for log_entry in completion_logs:
        ed = log_entry.extra_data or {}
        aid = ed.get('action_id', '')
        if not aid:
            continue
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

        item = {
            'type': 'completed',
            'action_id': aid,
            'action_uuid': action_uuid_map.get(aid, ''),
            'completed_at': completed_at,
            'duration_sec': duration_sec,
            'status': status,
            'entry_id': entry_uuid,
            'tracking': tracking,
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
        id=str(uuid.uuid4()),
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
