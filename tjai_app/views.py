import json
import time
from datetime import datetime, timedelta

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from django.http import Http404
from .models import Context, Entry, Tag, SubNote, Machine, SysConfig


def api_health(request):
    """Health check endpoint."""
    return JsonResponse({"status": "ok"})


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
                "timestamp_modified": now,  # Use server time for consistent ordering
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
                "timestamp_modified": now,  # Use server time for consistent ordering
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

    # Upsert tags (delete existing for entry, then insert)
    for tag in data.get("tags", []):
        Tag.objects.update_or_create(
            tag_name=tag["tag_name"],
            entry_id=tag["entry_id"],
        )
        counts["tags"] += 1

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

    return JsonResponse({"status": "ok", "received": counts})


@csrf_exempt
@require_http_methods(["GET"])
def sync_pull(request):
    """
    Return entries modified since a given timestamp.

    Query params:
        since: Unix timestamp (float). Returns entries with timestamp_modified > since.
        machine_id: Client machine ID (for tracking).

    Response:
    {
        "status": "ok",
        "server_time": <current server timestamp>,
        "entries": [...],
        "contexts": [...],
        "tags": [...],
        "sub_notes": [...]
    }
    """
    since = float(request.GET.get("since", 0))
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

    # Get modified contexts
    contexts = list(
        Context.objects.filter(timestamp_modified__gt=since).values(
            "name", "title", "description", "timestamp_created", "timestamp_modified"
        )
    )

    # Get modified entries
    entries = list(
        Entry.objects.filter(timestamp_modified__gt=since).values(
            "id", "parent_id", "content", "kind", "timestamp_created",
            "timestamp_modified", "context_id", "deleted_at", "name",
            "priority", "status", "data"
        )
    )
    # Rename context_id to context for client compatibility
    for e in entries:
        e["context"] = e.pop("context_id")

    # Get tags for modified entries
    entry_ids = [e["id"] for e in entries]
    tags = list(
        Tag.objects.filter(entry_id__in=entry_ids).values("tag_name", "entry_id")
    )

    # Get modified sub_notes
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
    now = time.time()
    # Get entries for next 30 days with event_date
    start_ts = now - (24 * 60 * 60)  # Include today even if past
    end_ts = now + (60 * 24 * 60 * 60)

    # Query journal entries with event_date in range
    # Data is proper JSON, use Django JSON field lookups
    entries = Entry.objects.filter(
        deleted_at__isnull=True,
        kind='journal',
        data__event_date__gte=start_ts,
        data__event_date__lt=end_ts,
    ).order_by('data__event_date')

    result = []
    for entry in entries:
        data = entry.data
        if isinstance(data, dict) and 'event_date' in data:
            result.append({
                'id': str(entry.id),
                'content': entry.content,
                'event_date': data['event_date'],
                'context': entry.context_id,
                'data': data,
            })

    return JsonResponse({'entries': result})


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

    # Recent entries (last 15, all types) - oldest first like tj l
    recent = Entry.objects.filter(
        deleted_at__isnull=True,
    ).order_by('-timestamp_modified')[:50]

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

    # Tags (alpha sorted)
    all_tags = list(Tag.objects.values_list('tag_name', flat=True).distinct().order_by('tag_name'))

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
    """Show all entries for a tag."""
    entry_ids = Tag.objects.filter(tag_name=tag_name).values_list('entry_id', flat=True)
    entries = Entry.objects.filter(
        id__in=entry_ids, deleted_at__isnull=True
    ).order_by('-timestamp_modified')[:100]
    return render(request, 'tjai_app/entry_list.html', {
        'title': f':{tag_name}',
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
