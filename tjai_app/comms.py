"""TJAI peer directory/mailbox services; peers retain the operator's MCP trust boundary.

Session IDs are provenance asserted by authenticated operator clients, not
independent proof of model identity. Peer messages never authorize actions.
"""

from datetime import timedelta
import json
import uuid

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .comms_models import LLMDelivery, LLMMessage, LLMSession


FRESH_SECONDS = 90
SESSION_STATES = {"idle", "active", "unknown", "offline"}
DELIVERY_STATES = {"pending", "written_to_transport", "accepted_by_client", "queued_in_client", "uncertain", "failed", "acknowledged"}


def _uuid(value):
    return str(uuid.UUID(value))


def _text(value, field, maximum, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"{field} must be {'nonempty ' if required else ''}text, at most {maximum} characters")
    return value


def _session(row):
    return {"id": row.id, "native_id": row.native_id, "name": row.name,
            "host": row.host, "client": row.client, "model": row.model,
            "cwd": row.cwd, "resources": row.resources, "delivery": row.delivery,
            "state": row.state, "last_seen": row.last_seen.isoformat(),
            "online": row.state != "offline" and row.last_seen >= timezone.now() - timedelta(seconds=FRESH_SECONDS)}


def register_session(native_id, name, host, client, model="", cwd="", resources=None, delivery="pull", state="idle"):
    native_id = _text(native_id, "native_id", 128)
    host = _text(host, "host", 160)
    client = _text(client, "client", 80)
    if state not in SESSION_STATES:
        raise ValueError("state must be idle, active, unknown or offline")
    if delivery not in {"pull", "codex_app_server", "codex_queue", "claude_socket"}:
        raise ValueError("Unsupported delivery capability")
    resources = resources or []
    if not isinstance(resources, list) or len(resources) > 20:
        raise ValueError("resources must be a list of at most 20 names")
    resources = sorted({_text(r, "resource", 160) for r in resources})
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["tjai:llm", client, host, native_id])))
    row, _ = LLMSession.objects.update_or_create(id=session_id, defaults={
        "native_id": native_id, "name": _text(name, "name", 160), "host": host,
        "client": client, "model": _text(model, "model", 120, False),
        "cwd": _text(cwd, "cwd", 4096, False), "resources": resources,
        "delivery": delivery, "state": state, "last_seen": timezone.now(),
    })
    return _session(row)


def heartbeat_session(session_id, state="idle", name=None, model=None, cwd=None):
    if state not in SESSION_STATES:
        raise ValueError("state must be idle, active, unknown or offline")
    changes = {"state": state, "last_seen": timezone.now()}
    for field, value, maximum in [("name", name, 160), ("model", model, 120), ("cwd", cwd, 4096)]:
        if value is not None:
            changes[field] = _text(value, field, maximum, field == "name")
    changed = LLMSession.objects.filter(id=_uuid(session_id)).update(**changes)
    if not changed:
        raise ValueError("Unknown session; register first")
    return {"session_id": session_id, "state": state}


def list_sessions(host=None, resource=None, include_offline=False, limit=100, offset=0):
    _page(limit, offset)
    rows = LLMSession.objects.order_by("host", "name", "id")
    if host:
        rows = rows.filter(host=host)
    if resource:
        rows = rows.filter(resources__contains=[resource])
    if not include_offline:
        rows = rows.exclude(state="offline").filter(last_seen__gte=timezone.now() - timedelta(seconds=FRESH_SECONDS))
    return [_session(row) for row in rows[offset:offset + limit]]


def _channel(session_id):
    return "tjai_comms_" + uuid.UUID(session_id).hex


def _notify(session_id):
    # PostgreSQL emits transactional notifications only after the rows commit.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_notify(%s, '')", [_channel(session_id)])


def _receipt(message):
    return {"message_id": message.id, "sender_id": message.sender_id,
            "sender": message.sender_snapshot, "content": message.content,
            "resource": message.resource, "reply_to": message.reply_to_id,
            "reply_requested": message.reply_requested, "created_at": message.created_at.isoformat(),
            "deliveries": [{"recipient_id": d.recipient_id, "state": d.state,
                            "updated_at": d.updated_at.isoformat(), "detail": d.detail,
                            "acknowledged_at": d.acknowledged_at.isoformat() if d.acknowledged_at else None}
                           for d in message.deliveries.all().order_by("recipient_id")]}


@transaction.atomic
def send_message(sender_id, content, message_id, recipient_id=None, resource=None, reply_to=None, reply_requested=False):
    sender_id, message_id = _uuid(sender_id), _uuid(message_id)
    content = _text(content, "content", 16000)
    if bool(recipient_id) == bool(resource):
        raise ValueError("Specify exactly one recipient_id or resource")
    if recipient_id:
        recipient_id = _uuid(recipient_id)
    resource = _text(resource or "", "resource", 160, False)
    reply_to = _uuid(reply_to) if reply_to else None
    # Serialize duplicate client IDs before checking their immutable envelope.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ["tjai-message:" + message_id])
    existing = LLMMessage.objects.filter(id=message_id).first()
    if existing:
        destinations = list(existing.deliveries.values_list("recipient_id", flat=True))
        if (existing.sender_id != sender_id or existing.content != content
                or existing.resource != resource or existing.reply_to_id != reply_to
                or existing.reply_requested != reply_requested
                or (recipient_id and destinations != [recipient_id])):
            raise ValueError("Message ID already exists with a different envelope")
        return _receipt(existing)
    sender = LLMSession.objects.get(id=sender_id)
    if recipient_id == sender_id:
        raise ValueError("Self-messaging is not supported")
    if recipient_id:
        recipients = [LLMSession.objects.get(id=recipient_id)]
    else:
        recipients = list(LLMSession.objects.filter(resources__contains=[resource],
                          last_seen__gte=timezone.now() - timedelta(seconds=FRESH_SECONDS))
                          .exclude(id=sender_id).exclude(state="offline").order_by("id")[:101])
        if len(recipients) > 100:
            raise ValueError("Resource group exceeds 100 recipients; narrow the destination")
        if not recipients:
            raise ValueError("No online peers are registered for that resource")
    if reply_to and not LLMDelivery.objects.filter(message_id=reply_to, recipient_id=sender_id).exists():
        raise ValueError("A reply must reference a message delivered to the sending session")
    now = timezone.now()
    message = LLMMessage.objects.create(id=message_id, sender=sender, sender_snapshot=_session(sender),
              content=content, resource=resource, reply_to_id=reply_to, reply_requested=reply_requested, created_at=now)
    LLMDelivery.objects.bulk_create([LLMDelivery(message=message, recipient=r, updated_at=now) for r in recipients])
    for recipient in recipients:
        _notify(recipient.id)
    if reply_to:
        acknowledge_message(sender_id, reply_to)
    return _receipt(message)


def _page(limit, offset):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")


def get_messages(session_id, direction="inbox", pending_only=True, limit=50, offset=0):
    session_id = _uuid(session_id)
    _page(limit, offset)
    if not LLMSession.objects.filter(id=session_id).exists():
        raise ValueError("Unknown session")
    if direction == "inbox":
        rows = LLMDelivery.objects.filter(recipient_id=session_id)
        if pending_only:
            rows = rows.filter(acknowledged_at__isnull=True)
        ids = rows.values_list("message_id", flat=True)
        messages = LLMMessage.objects.filter(id__in=ids)
    elif direction == "sent":
        messages = LLMMessage.objects.filter(sender_id=session_id)
        if pending_only:
            messages = messages.filter(deliveries__acknowledged_at__isnull=True).distinct()
    else:
        raise ValueError("direction must be inbox or sent")
    return [_receipt(m) for m in messages.order_by("created_at", "id")[offset:offset + limit]]


@transaction.atomic
def record_delivery(session_id, message_id, state, detail="", claim=False):
    if state not in DELIVERY_STATES - {"pending", "acknowledged"}:
        raise ValueError("Use acknowledge_message for model acknowledgment")
    row = LLMDelivery.objects.select_for_update().get(recipient_id=_uuid(session_id), message_id=_uuid(message_id))
    if claim:
        if state != "uncertain":
            raise ValueError("A dispatch claim must use state uncertain")
        if row.state != "pending":
            return {"claimed": False, "state": row.state, "message_id": row.message_id}
    # An adapter report arriving after a model acknowledgment cannot undo it.
    if row.acknowledged_at is None:
        row.state, row.detail, row.updated_at = state, _text(detail, "detail", 2000, False), timezone.now()
        row.save(update_fields=["state", "detail", "updated_at"])
    result = _receipt(row.message)
    if claim:
        result["claimed"] = True
    return result


@transaction.atomic
def acknowledge_message(session_id, message_id):
    row = LLMDelivery.objects.select_for_update().get(recipient_id=_uuid(session_id), message_id=_uuid(message_id))
    if row.acknowledged_at is None:
        row.state, row.acknowledged_at, row.updated_at = "acknowledged", timezone.now(), timezone.now()
        row.save(update_fields=["state", "acknowledged_at", "updated_at"])
    from .comms_dialog import record_peer_dialog
    record_peer_dialog(row, row.acknowledged_at.timestamp())
    return {"message_id": row.message_id, "recipient_id": row.recipient_id,
            "state": row.state, "acknowledged_at": row.acknowledged_at.isoformat()}


async def wait_messages(session_id, wait_seconds=25, limit=50):
    """Finite async wait for new pending deliveries; no model or web-worker polling."""
    import psycopg
    from psycopg import sql

    session_id = _uuid(session_id)
    if not isinstance(wait_seconds, (int, float)) or not 0 <= wait_seconds <= 25:
        raise ValueError("wait_seconds must be between 0 and 25")
    _page(limit, 0)
    database = settings.DATABASES["default"]
    params = {p: database.get(k) for p, k in [("dbname", "NAME"), ("user", "USER"),
              ("password", "PASSWORD"), ("host", "HOST"), ("port", "PORT")] if database.get(k)}
    params.update({k: v for k, v in database.get("OPTIONS", {}).items() if k in {"sslmode", "sslrootcert", "sslcert", "sslkey", "service", "options"}})

    def pending():
        # Adapter receive excludes already-handed messages. Unacknowledged
        # messages remain available to the model through get_messages.
        if not LLMSession.objects.filter(id=session_id).exists():
            raise ValueError("Unknown session")
        ids = LLMDelivery.objects.filter(recipient_id=session_id, state="pending").values_list("message_id", flat=True)
        return [_receipt(m) for m in LLMMessage.objects.filter(id__in=ids).order_by("created_at", "id")[:limit]]

    if not wait_seconds:
        return await sync_to_async(pending)()
    async with await psycopg.AsyncConnection.connect(**params, autocommit=True, connect_timeout=5) as listener:
        await listener.execute(sql.SQL("LISTEN {}").format(sql.Identifier(_channel(session_id))))
        # Subscribe before inspecting durable rows to avoid a missed-wake race.
        rows = await sync_to_async(pending)()
        if rows:
            return rows
        async for _ in listener.notifies(timeout=wait_seconds, stop_after=1):
            break
        return await sync_to_async(pending)()
