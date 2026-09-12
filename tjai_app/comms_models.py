"""Durable directory and mailbox for independent LLM sessions."""

from django.db import models


class LLMSession(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    native_id = models.CharField(max_length=128)
    name = models.CharField(max_length=160)
    host = models.CharField(max_length=160)
    client = models.CharField(max_length=80)
    model = models.CharField(max_length=120, blank=True)
    cwd = models.TextField(blank=True)
    resources = models.JSONField(default=list)
    delivery = models.CharField(max_length=40, default="pull")
    state = models.CharField(max_length=20, default="idle")
    last_seen = models.DateTimeField()

    class Meta:
        db_table = "llm_sessions"
        indexes = [models.Index(fields=["last_seen"], name="llm_sessions_seen")]


class LLMMessage(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    sender = models.ForeignKey(LLMSession, on_delete=models.PROTECT, related_name="sent_messages")
    sender_snapshot = models.JSONField(default=dict)
    content = models.TextField()
    resource = models.CharField(max_length=160, blank=True)
    reply_to = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True)
    reply_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "llm_messages"
        ordering = ["created_at", "id"]


class LLMDelivery(models.Model):
    message = models.ForeignKey(LLMMessage, on_delete=models.PROTECT, related_name="deliveries")
    recipient = models.ForeignKey(LLMSession, on_delete=models.PROTECT, related_name="inbox")
    state = models.CharField(max_length=32, default="pending")
    updated_at = models.DateTimeField()
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    detail = models.TextField(blank=True)

    class Meta:
        db_table = "llm_deliveries"
        constraints = [models.UniqueConstraint(fields=["message", "recipient"], name="llm_delivery_recipient")]
        indexes = [models.Index(fields=["recipient", "state"], name="llm_inbox_state")]
