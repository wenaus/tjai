import json
import time
import uuid
from datetime import datetime, timedelta

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from django.db.models.functions import Lower
from django.http import Http404
from django.conf import settings as django_settings
from .models import Context, Entry, Tag, TagStats, SubNote, Machine, SysConfig


def api_health(request):
    """Health check endpoint."""
    return JsonResponse({"status": "ok"})


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
            except json.JSONDecodeError:
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
                pass

    # Upsert sub_notes
    for note in data.get("sub_notes", []):
        note_data = note.get("data")
        if isinstance(note_data, str):
            try:
                note_data = json.loads(note_data)
            except json.JSONDecodeError:
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
            "priority", "status", "data"
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
    return render(request, 'tjai_app/dashboard.html')


@login_required
def dashboard_calendar(request):
    """Return calendar data as JSON for dashboard."""
    import zoneinfo

    now = time.time()

    # Get timezone from SysConfig
    tz_config = SysConfig.objects.filter(key='timezone').first()
    timezone_name = tz_config.value if tz_config else 'America/New_York'
    try:
        tz = zoneinfo.ZoneInfo(timezone_name)
    except Exception:
        tz = None

    # Go back 7 days, then to Monday of that week (to show full previous week)
    seven_days_ago = datetime.now() - timedelta(days=7)
    days_since_monday = seven_days_ago.weekday()
    monday_of_prev_week = seven_days_ago - timedelta(days=days_since_monday)
    start_ts = monday_of_prev_week.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    end_ts = now + (60 * 24 * 60 * 60)

    # Query journal entries with event_date in range
    entries = Entry.objects.filter(
        deleted_at__isnull=True,
        kind='journal',
        data__event_date__gte=start_ts,
        data__event_date__lt=end_ts,
    ).order_by('data__event_date')

    # Calculate today's date key in configured timezone
    if tz:
        now_dt = datetime.now(tz)
    else:
        now_dt = datetime.now()
    today_date_str = now_dt.strftime('%Y%m%d')

    result = []
    for entry in entries:
        data = entry.data
        if isinstance(data, dict) and 'event_date' in data:
            event_ts = data['event_date']
            # Convert to timezone-aware datetime for formatting
            if tz:
                event_dt = datetime.fromtimestamp(event_ts, tz=tz)
            else:
                event_dt = datetime.fromtimestamp(event_ts)

            # Pre-format all date/time strings server-side
            date_key = event_dt.strftime('%Y%m%d')
            date_display = event_dt.strftime('%a %b %d')  # "Mon Feb 09"
            time_display = event_dt.strftime('%H:%M') if (event_dt.hour or event_dt.minute) else None
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

    return JsonResponse({
        'entries': result,
        'server_time': now,
        'timezone': timezone_name,
        'today_date': today_date_str,
    })


@login_required
def dashboard_status(request):
    """Return status data as JSON for dashboard."""
    now = time.time()
    now_dt = datetime.now()

    # Format timestamp
    timestamp = now_dt.strftime('%a %m/%d/%H:%M')

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
    from zoneinfo import ZoneInfo
    tz = ZoneInfo('America/New_York')
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
    recent = Entry.objects.filter(
        deleted_at__isnull=True,
    ).exclude(
        status='archive'
    ).order_by('-timestamp_modified')

    # Batch fetch tags for all entries
    entry_ids = [e.id for e in recent]
    tags_by_entry = {}
    for t in Tag.objects.filter(entry_id__in=entry_ids):
        tags_by_entry.setdefault(t.entry_id, []).append(t.tag_name)

    recent_entries = []
    for e in recent:
        lines = e.content.split('\n')
        line_count = len([l for l in lines if l.strip()])
        # Get tags not already in content
        entry_tags = tags_by_entry.get(e.id, [])
        missing_tags = [t for t in entry_tags if f':{t}' not in e.content]
        data = e.data if isinstance(e.data, dict) else None
        recent_entries.append({
            'id': e.id,
            'content': lines[0],
            'kind': e.kind,
            'context': e.context_id,
            'timestamp': e.timestamp_modified,
            'line_count': line_count if line_count > 1 else None,
            'name': e.name,
            'event_date': data.get('event_date') if data else None,
            'tags': missing_tags,
        })

    # Get timezone from SysConfig, default to America/New_York
    tz_config = SysConfig.objects.filter(key='timezone').first()
    timezone_name = tz_config.value if tz_config else 'America/New_York'

    # Contexts (alpha sorted)
    from django.db.models import Count
    contexts = list(Entry.objects.filter(
        deleted_at__isnull=True, context_id__isnull=False
    ).values_list('context_id', flat=True).distinct().order_by('context_id'))

    # Tags (alpha sorted, excluding context-only tags)
    all_tags = list(TagStats.objects.filter(is_context_only=False)
        .values_list('tag_name', flat=True).order_by('tag_name'))

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
        'timezone': timezone_name,
        'contexts': contexts,
        'all_tags': all_tags,
        'open_todos': open_todos,
        'machines': machines,
        'oldest_sync': {'machine': oldest_machine, 'age_min': sync_age_min} if oldest_machine else None,
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
            'line_count': len([l for l in lines if l.strip()]),
        })

    return JsonResponse({'entries': result})


@login_required
def entry_detail(request, entry_id):
    """Show single entry detail page."""
    import markdown
    entry = Entry.objects.filter(id=entry_id, deleted_at__isnull=True).first()
    if not entry:
        raise Http404("Entry not found")
    tags = list(Tag.objects.filter(entry_id=entry_id).values_list('tag_name', flat=True))
    lines = [l for l in entry.content.split('\n') if l.strip()]
    data = entry.data if isinstance(entry.data, dict) else None
    content_html = markdown.markdown(entry.content)
    # Linkify bare URLs not already in anchor tags
    import re
    content_html = re.sub(
        r'(?<!["\'>])(https?://[^\s<]+)',
        r'<a href="\1">\1</a>',
        content_html
    )
    first_line = lines[0] if lines else ''
    return render(request, 'tjai_app/entry_detail.html', {
        'entry': entry,
        'content_html': content_html,
        'tags': tags,
        'line_count': len(lines),
        'first_line': first_line,
        'event_date': data.get('event_date') if data else None,
    })


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
        e.line_count = len(lines) if len(lines) > 1 else None
        entry_tags = tags_by_entry.get(e.id, [])
        e.display_tags = [t for t in entry_tags if f':{t}' not in e.first_line]
        # Convert float timestamp to datetime for template formatting
        e.modified_dt = datetime.fromtimestamp(e.timestamp_modified)
        result.append(e)
    return result


@login_required
def context_entries(request, context_name):
    """Show all entries for a context."""
    entries = Entry.objects.filter(
        context_id=context_name, deleted_at__isnull=True
    ).order_by('-timestamp_modified')[:100]
    return render(request, 'tjai_app/entry_list.html', {
        'title': f'={context_name}',
        'entries': _entries_for_list(entries),
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
        ).order_by('-timestamp_modified')[:100]
        title = '(none)'
    else:
        entry_ids = Tag.objects.filter(tag_name=tag_name).values_list('entry_id', flat=True)
        entries = Entry.objects.filter(
            id__in=entry_ids, deleted_at__isnull=True
        ).order_by('-timestamp_modified')[:100]
        title = f':{tag_name}'
    return render(request, 'tjai_app/entry_list.html', {
        'title': title,
        'entries': _entries_for_list(entries),
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
    ).order_by('-timestamp_modified')[:100]
    label = kind_labels.get(kind_name, kind_name)
    return render(request, 'tjai_app/entry_list.html', {
        'title': f'[{kind_name}] {label}',
        'entries': _entries_for_list(entries),
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
        return JsonResponse({
            "status": "duplicate",
            "entry_id": duplicate.id,
            "content": duplicate.content,
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
    """Create a journal entry from an external source (Gmail Add-on).

    Requires Bearer token matching SysConfig 'gmail_addon_api_key'.

    Request body: {title, event_timestamp, location (optional)}
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
    location = data.get("location", "").strip()

    if not title:
        return JsonResponse({"error": "title is required"}, status=400)
    if not isinstance(event_timestamp, (int, float)):
        return JsonResponse({"error": "event_timestamp must be a number"}, status=400)

    parts = [title]
    if location:
        parts.append(f"@ {location}")
    if zoom_url:
        parts.append(f"[Zoom]({zoom_url})")
    if gmail_url:
        parts.append(f"[Gmail]({gmail_url})")
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
    Tag.objects.create(tag_name='gmail', entry=entry)

    return JsonResponse({
        "status": "ok",
        "entry_id": entry.id,
        "content": content,
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
