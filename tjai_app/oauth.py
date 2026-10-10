"""OAuth 2.1 sign-in for the OAuth-only MCP endpoint (docs/mcp.md § OAuth).

ChatGPT's connectors cannot send a fixed token, so /tjai/mcp-oauth/ accepts
only tokens issued here: authorization code with PKCE (S256), dynamic client
registration, refresh-token rotation and revocation, served by the MCP SDK's
handlers in the MCP process. Approval is a Django page behind tjai's staff
login. The fixed-token endpoint /tjai/mcp/ does not use any of this.
"""

import functools
import hashlib
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from asgiref.sync import sync_to_async
from django.db import OperationalError, connections
from mcp.server.auth.handlers.authorize import AuthorizationHandler
from mcp.server.auth.handlers.metadata import MetadataHandler, ProtectedResourceMetadataHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.handlers.revoke import RevocationHandler
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken as TokenResponse, ProtectedResourceMetadata
from pydantic import AnyHttpUrl
from starlette.routing import Route

from .oauth_models import OAuthClient, OAuthGrant, OAuthToken

ORIGIN = "https://etaverse.com"
ISSUER = f"{ORIGIN}/tjai/oauth"
RESOURCE = f"{ORIGIN}/tjai/mcp-oauth"
SCOPE = "tjai"
RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource/tjai/mcp-oauth"
SERVER_METADATA_PATH = "/.well-known/oauth-authorization-server/tjai/oauth"
APPROVE_PATH = "/tjai/oauth-approve/"
AUTH_METHODS = ["client_secret_post", "client_secret_basic", "none"]

# Registration accepts callbacks only on these hosts, so a client registered
# by anyone can deliver a code only to one of these services.
REDIRECT_HOSTS = {"chatgpt.com", "claude.ai", "claude.com"}

REQUEST_SECONDS = 600
CODE_SECONDS = 300
ACCESS_SECONDS = 3600
REFRESH_SECONDS = 90 * 86400


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def same_resource(value):
    """True for the OAuth endpoint's URL, with or without its trailing slash."""
    return value is None or value.rstrip("/") == RESOURCE


def _db(func):
    """Run ORM work off the event loop, retrying once after a dropped connection."""
    @functools.wraps(func)
    def retrying(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except OperationalError:
            connections.close_all()
            return func(*args, **kwargs)
    return sync_to_async(retrying)


class GrantCode(AuthorizationCode):
    grant_id: str


@_db
def _get_client(client_id):
    row = OAuthClient.objects.filter(client_id=client_id).first()
    return OAuthClientInformationFull.model_validate(row.info) if row else None


@_db
def _save_client(info):
    OAuthClient.objects.create(
        client_id=info.client_id, info=info.model_dump(mode="json"), created_at=_now()
    )


@_db
def _create_grant(request_id, client_id, params):
    OAuthGrant.objects.create(
        id=request_id, client_id=client_id, params=params.model_dump(mode="json"), created_at=_now()
    )


@_db
def _load_code(client_id, code):
    grant = OAuthGrant.objects.filter(code_hash=_hash(code), client_id=client_id).first()
    if grant is None:
        return None
    if grant.status == "used":
        # A replayed code: withdraw what the first exchange issued (RFC 6749 §4.1.2).
        OAuthToken.objects.filter(grant=grant).update(revoked=True)
        return None
    if grant.status != "approved":
        return None
    p = grant.params
    return GrantCode(
        code=code,
        grant_id=grant.id,
        scopes=p.get("scopes") or [SCOPE],
        expires_at=grant.code_expires_at,
        client_id=client_id,
        code_challenge=p["code_challenge"],
        redirect_uri=p["redirect_uri"],
        redirect_uri_provided_explicitly=p["redirect_uri_provided_explicitly"],
        resource=p.get("resource"),
    )


def _issue(grant, scopes):
    """A fresh access and refresh token pair for a grant."""
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now, stamp = time.time(), _now()
    OAuthToken.objects.bulk_create([
        OAuthToken(token_hash=_hash(access), kind="access", grant=grant, scopes=scopes,
                   resource=RESOURCE, expires_at=now + ACCESS_SECONDS, created_at=stamp),
        OAuthToken(token_hash=_hash(refresh), kind="refresh", grant=grant, scopes=scopes,
                   resource=RESOURCE, expires_at=now + REFRESH_SECONDS, created_at=stamp),
    ])
    return TokenResponse(
        access_token=access, expires_in=ACCESS_SECONDS, scope=" ".join(scopes), refresh_token=refresh
    )


@_db
def _exchange_code(code):
    if not OAuthGrant.objects.filter(id=code.grant_id, status="approved").update(status="used"):
        raise TokenError("invalid_grant", "authorization code already used")
    return _issue(OAuthGrant.objects.get(id=code.grant_id), code.scopes)


@_db
def _load_refresh(client_id, token):
    row = OAuthToken.objects.filter(
        token_hash=_hash(token), kind="refresh", revoked=False, grant__client_id=client_id
    ).first()
    if row is None:
        return None
    return RefreshToken(token=token, client_id=client_id, scopes=row.scopes, expires_at=int(row.expires_at))


@_db
def _rotate(refresh, scopes):
    """Spend a refresh token for a new pair; the old access token runs out on its own."""
    row = OAuthToken.objects.select_related("grant").get(token_hash=_hash(refresh.token))
    if not OAuthToken.objects.filter(token_hash=row.token_hash, revoked=False).update(revoked=True):
        raise TokenError("invalid_grant", "refresh token already used")
    return _issue(row.grant, scopes)


@_db
def _load_access(token):
    row = OAuthToken.objects.select_related("grant").filter(
        token_hash=_hash(token), kind="access", revoked=False
    ).first()
    if row is None or row.expires_at < time.time():
        return None
    return AccessToken(token=token, client_id=row.grant.client_id, scopes=row.scopes,
                       expires_at=int(row.expires_at), resource=row.resource)


@_db
def _revoke(token):
    row = OAuthToken.objects.filter(token_hash=_hash(token)).first()
    if row:
        OAuthToken.objects.filter(grant_id=row.grant_id).update(revoked=True)


class Provider:
    """tjai as its own authorization server, behind the MCP SDK's handlers."""

    async def get_client(self, client_id):
        return await _get_client(client_id)

    async def register_client(self, client_info):
        for uri in client_info.redirect_uris or []:
            parsed = urlparse(str(uri))
            if parsed.scheme != "https" or parsed.hostname not in REDIRECT_HOSTS:
                raise RegistrationError(
                    "invalid_redirect_uri",
                    f"callbacks are accepted only on {', '.join(sorted(REDIRECT_HOSTS))}",
                )
        if client_info.token_endpoint_auth_method not in AUTH_METHODS:
            raise RegistrationError(
                "invalid_client_metadata", f"token_endpoint_auth_method must be one of {AUTH_METHODS}"
            )
        await _save_client(client_info)

    async def authorize(self, client, params):
        if not same_resource(params.resource):
            raise AuthorizeError("invalid_request", f"tokens are issued only for {RESOURCE}")
        request_id = secrets.token_urlsafe(32)
        await _create_grant(request_id, client.client_id, params)
        return f"{ORIGIN}{APPROVE_PATH}{request_id}/"

    async def load_authorization_code(self, client, authorization_code):
        return await _load_code(client.client_id, authorization_code)

    async def exchange_authorization_code(self, client, authorization_code):
        return await _exchange_code(authorization_code)

    async def load_refresh_token(self, client, refresh_token):
        return await _load_refresh(client.client_id, refresh_token)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        return await _rotate(refresh_token, scopes)

    async def load_access_token(self, token):
        return await _load_access(token)

    async def revoke_token(self, token):
        await _revoke(token.token)


async def access_allowed(token):
    """Whether a bearer token may use the OAuth endpoint."""
    access = await _load_access(token)
    return access is not None and same_resource(access.resource) and SCOPE in access.scopes


def challenge(error=None):
    """WWW-Authenticate value pointing clients at the sign-in metadata (RFC 9728 §5.1)."""
    value = f'Bearer resource_metadata="{ORIGIN}{RESOURCE_METADATA_PATH}", scope="{SCOPE}"'
    if error:
        value += f', error="{error}"'
    return value


def is_oauth_path(path):
    """Paths that reach the MCP process only through the OAuth proxy rules."""
    return path.startswith(("/.well-known/oauth-protected-resource/tjai/",
                            "/.well-known/oauth-authorization-server/tjai/", "/tjai/oauth/"))


def routes():
    provider = Provider()
    authenticator = ClientAuthenticator(provider)
    registration = ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE])
    revocation = RevocationOptions(enabled=True)
    server = build_metadata(AnyHttpUrl(ISSUER), None, registration, revocation)
    server.token_endpoint_auth_methods_supported = AUTH_METHODS
    server.revocation_endpoint_auth_methods_supported = AUTH_METHODS
    resource = ProtectedResourceMetadata(
        resource=AnyHttpUrl(RESOURCE), authorization_servers=[AnyHttpUrl(ISSUER)],
        scopes_supported=[SCOPE], resource_name="tjai",
    )
    # The same document for clients that keep the endpoint's trailing slash.
    resource_slash = resource.model_copy(update={"resource": AnyHttpUrl(RESOURCE + "/")})
    get, post = ["GET", "OPTIONS"], ["POST", "OPTIONS"]
    return [
        Route(RESOURCE_METADATA_PATH,
              cors_middleware(ProtectedResourceMetadataHandler(resource).handle, get), methods=get),
        Route(RESOURCE_METADATA_PATH + "/",
              cors_middleware(ProtectedResourceMetadataHandler(resource_slash).handle, get), methods=get),
        Route(SERVER_METADATA_PATH, cors_middleware(MetadataHandler(server).handle, get), methods=get),
        Route("/tjai/oauth/authorize", AuthorizationHandler(provider).handle, methods=["GET", "POST"]),
        Route("/tjai/oauth/token",
              cors_middleware(TokenHandler(provider, authenticator).handle, post), methods=post),
        Route("/tjai/oauth/register",
              cors_middleware(RegistrationHandler(provider, registration).handle, post), methods=post),
        Route("/tjai/oauth/revoke",
              cors_middleware(RevocationHandler(provider, authenticator).handle, post), methods=post),
    ]


def pending_grant(request_id):
    """The approval request with this id while it still awaits a decision."""
    grant = OAuthGrant.objects.select_related("client").filter(id=request_id, status="pending").first()
    if grant is None or (_now() - grant.created_at).total_seconds() > REQUEST_SECONDS:
        return None
    return grant


def describe(grant):
    """What the approve page shows about a request."""
    return {
        "client_name": grant.client.info.get("client_name") or "An unnamed client",
        "callback_host": urlparse(grant.params["redirect_uri"]).hostname,
        "scopes": grant.params.get("scopes") or [SCOPE],
    }


def decide(grant, user, approve):
    """Record the decision; return the client's callback URL carrying it, or None if already decided."""
    if approve:
        code = secrets.token_urlsafe(32)
        changes = {"status": "approved", "code_hash": _hash(code), "code_expires_at": time.time() + CODE_SECONDS}
        result = {"code": code}
    else:
        changes = {"status": "denied"}
        result = {"error": "access_denied"}
    if not OAuthGrant.objects.filter(id=grant.id, status="pending").update(decided_by=user, **changes):
        return None
    return construct_redirect_uri(
        grant.params["redirect_uri"], **result, state=grant.params.get("state"), iss=ISSUER
    )
