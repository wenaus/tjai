"""OAuth clients, grants and tokens for the OAuth-only MCP endpoint (docs/mcp.md § OAuth)."""

from django.conf import settings
from django.db import models


class OAuthClient(models.Model):
    """A client registered through dynamic client registration (RFC 7591)."""
    client_id = models.CharField(max_length=64, primary_key=True)
    info = models.JSONField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = "oauth_clients"


class OAuthGrant(models.Model):
    """One authorization request: pending until approved, then its single-use code.

    Every token issued from it, including those rotated by refresh, points back
    here, so revoking a grant revokes the whole family.
    """
    id = models.CharField(max_length=64, primary_key=True)
    client = models.ForeignKey(OAuthClient, on_delete=models.PROTECT, related_name="grants")
    params = models.JSONField()
    status = models.CharField(max_length=10, default="pending")  # pending, approved, denied, used
    code_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    code_expires_at = models.FloatField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True
    )
    created_at = models.DateTimeField()

    class Meta:
        db_table = "oauth_grants"


class OAuthToken(models.Model):
    """An issued access or refresh token, stored by its SHA-256 only."""
    token_hash = models.CharField(max_length=64, primary_key=True)
    kind = models.CharField(max_length=10)  # access, refresh
    grant = models.ForeignKey(OAuthGrant, on_delete=models.PROTECT, related_name="tokens")
    scopes = models.JSONField(default=list)
    resource = models.CharField(max_length=255)
    expires_at = models.FloatField()
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "oauth_tokens"
