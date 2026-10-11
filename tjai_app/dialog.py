"""Shared ingestion for authenticated dialog hooks and MCP recording."""

import json
import re
import time
import uuid
from datetime import datetime

from django.conf import settings
from django.db import transaction

from .dialog_context import CURRENT_DIALOG_CONTEXT, DIALOG_TAG
from .models import Context, Entry, SysConfig, Tag

# Text the harness writes into a user turn (a task notification, a client's own agent prompt) is not
# Torre's words: it is recorded with role 'harness', never as his turn.
HARNESS_PREFIXES = ('<task-notification>', '## Memory Writing Agent')


def is_research_agent_turn(data):
    """The research agent's Claude runs in the server's own tree; no other session does."""
    return (data.get("project_path") or "").rstrip("/") == str(settings.BASE_DIR).rstrip("/")


class DialogValidationError(ValueError):
    """A validation failure reported as HTTP 400 by the legacy REST API."""


def record_dialog(data):
    """Record one dialog turn; transports authenticate before calling this.

    Preserve source timestamps, stable retry identity, hook metadata, peer
    recognition and research attribution, including legacy validation behavior.
    DialogValidationError represents the REST API's existing HTTP 400 responses.
    Legacy hooks may omit timestamp and source_id; MCP adds schema validation.
    """
    content = data.get("content", "").strip()
    role = data.get("role", "").strip()

    if not content:
        raise DialogValidationError("content is required")
    if role not in ("user", "assistant"):
        raise DialogValidationError("role must be 'user' or 'assistant'")

    now = time.time()
    recorded_at = now
    source_timestamp = data.get("timestamp")
    if source_timestamp is not None:
        try:
            stamp = datetime.fromisoformat(source_timestamp.replace('Z', '+00:00'))
            if stamp.utcoffset() is None:
                raise ValueError("timestamp requires a timezone")
            recorded_at = stamp.timestamp()
        except (ValueError, TypeError, AttributeError, OverflowError) as e:
            raise DialogValidationError(f"invalid timestamp: {e}") from e
    source_id = data.get("source_id") or ""
    if not isinstance(source_id, str) or len(source_id) > 512:
        raise DialogValidationError("source_id must be a string of at most 512 characters")
    if source_id and not all(data.get(k) for k in ("hostname", "client", "session_id")):
        raise DialogValidationError("source_id requires hostname, client and session_id")
    if role == "user":
        from .comms_dialog import recorded_native_peer
        peer_entry = recorded_native_peer(content, data, recorded_at)
        if peer_entry:
            return {"status": "ok", "entry_id": peer_entry.id}
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
    if source_id:
        entry_data["source_id"] = source_id
    if source_timestamp is not None:
        entry_data["source_timestamp"] = source_timestamp
    if data.get("content_type"):
        entry_data["content_type"] = data["content_type"]
    if role == "user" and content.startswith(HARNESS_PREFIXES):
        entry_data["role"] = "harness"

    extra_tags = []

    # Detect research subagent products: only the research agent's own task notifications. Every
    # session's harness writes task notifications; attributing them all to the running research topic
    # filed hundreds of other sessions' turns under it.
    if content.startswith('<task-notification>') and is_research_agent_turn(data):
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
    # Stable primary key makes retries (including a lost HTTP response) and
    # overlapping async hooks idempotent without a content/time dedup window.
    identity = json.dumps(["tjai-dialog-v1", data.get("hostname"), data.get("client"),
                           data.get("session_id"), role, source_id])
    entry_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity) if source_id else uuid.uuid7())
    with transaction.atomic():
        entry, created = Entry.objects.get_or_create(id=entry_id, defaults={
            "content": content,
            "kind": "memory",
            "context_id": CURRENT_DIALOG_CONTEXT,
            "timestamp_created": recorded_at,
            "timestamp_modified": now,
            "is_dirty": 0,
            "data": entry_data,
        })
        if created:
            Tag.objects.create(tag_name=DIALOG_TAG, entry=entry)
            for tag in extra_tags:
                Tag.objects.create(tag_name=tag, entry=entry)

    return {"status": "ok", "entry_id": entry.id}
