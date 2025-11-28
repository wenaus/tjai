import json
import time

from django.http import JsonResponse
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
                "timestamp_modified": ctx["timestamp_modified"],
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
                "timestamp_modified": entry["timestamp_modified"],
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
