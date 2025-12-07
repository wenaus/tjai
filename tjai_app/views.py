import json
import time
from datetime import datetime, timedelta

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Context, Entry, Tag, SubNote, SyncMetadata, Machine, SysConfig


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
                "data": entry.get("data"),
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
        SubNote.objects.update_or_create(
            id=note["id"],
            defaults={
                "parent_id": note["parent_id"],
                "content": note["content"],
                "timestamp_created": note["timestamp_created"],
                "data": note.get("data"),
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


def dashboard(request):
    """Render the dashboard HTML page."""
    return render(request, 'tjai_app/dashboard.html')


def dashboard_calendar(request):
    """Return calendar data as JSON for dashboard."""
    now = time.time()
    # Get entries for next 30 days with event_date
    end_ts = now + (30 * 24 * 60 * 60)

    entries = Entry.objects.filter(
        deleted_at__isnull=True,
        kind='journal',
        data__event_date__isnull=False,
    ).order_by('data__event_date')

    # Filter by event_date range and build response
    result = []
    for entry in entries:
        if entry.data and 'event_date' in entry.data:
            event_date = entry.data['event_date']
            if now - (24*60*60) <= event_date < end_ts:  # Include today even if past
                result.append({
                    'id': entry.id,
                    'content': entry.content,
                    'event_date': event_date,
                    'context': entry.context_id,
                    'data': entry.data,
                })

    # Sort by event_date
    result.sort(key=lambda x: x['event_date'])

    return JsonResponse({'entries': result})


def dashboard_status(request):
    """Return status data as JSON for dashboard."""
    now = time.time()
    now_dt = datetime.now()

    # Format timestamp
    timestamp = now_dt.strftime('%a %m/%d/%H:%M')

    # Get current context from most recent entry or default
    # For dashboard, we show general status, not a specific machine's context
    context = None
    context_description = None

    # Clock status - find latest clock start
    try:
        latest_clock_meta = SyncMetadata.objects.filter(key='latest_clock_start_id').first()
        clock_data = None
        if latest_clock_meta:
            clock_entry = Entry.objects.filter(id=latest_clock_meta.value).first()
            if clock_entry and clock_entry.data and clock_entry.data.get('clock') == 'start':
                start_time = clock_entry.data.get('event_date', clock_entry.timestamp_created)
                breaks_min = clock_entry.data.get('breaks', 0)
                stop_id = clock_entry.data.get('stop_id')

                if stop_id:
                    # Stopped - get stop time
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
    except Exception:
        clock_data = None

    # Recent entries (last 10, non-journal)
    recent = Entry.objects.filter(
        deleted_at__isnull=True,
    ).exclude(
        kind='journal'
    ).order_by('-timestamp_created')[:10]

    recent_entries = [{
        'content': e.content,
        'kind': e.kind,
        'context': e.context_id,
    } for e in recent]

    return JsonResponse({
        'timestamp': timestamp,
        'context': context,
        'context_description': context_description,
        'clock': clock_data,
        'recent_entries': recent_entries,
    })
