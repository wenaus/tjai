# Next Steps

## CRITICAL: OAuth Implementation is BROKEN

**Date:** 2026-01-25

### Current State - BOTH ACCESS METHODS BROKEN

1. **Claude Code (CLI) - BROKEN**: Returns 401 Unauthorized for all requests
2. **Claude.ai (OAuth) - BROKEN**: Gets to Auth0 login, fails after authentication

### Root Cause

The middleware at `tjai_app/middleware.py` blocks ALL requests to `/tjai/mcp*` when `AUTH0_DOMAIN` is set in the environment. This breaks Claude Code which doesn't use OAuth.

### What Was Attempted

1. **Path mismatch fix** - Middleware now correctly checks `/tjai/mcp` (with FORCE_SCRIPT_NAME)
2. **Trailing slash fix** - Added URL pattern for both `/mcp` and `/mcp/` in urls.py
3. **Auth0 app type change** - Changed from "Regular Web Application" to "Single Page Application" (tokens were JWE instead of JWT)
4. **Multiple slash/no-slash iterations** - Auth0 API identifier is locked at `https://etaverse.com/tjai/mcp` (NO trailing slash, CANNOT be changed)

### The Fix That Needs to Be Applied

The middleware needs to distinguish between:
- **Claude Code**: POST requests without auth → allow through
- **Claude.ai**: GET requests trigger OAuth discovery → return 401 with WWW-Authenticate

In `tjai_app/middleware.py`, after the token check section, replace:

```python
# No token - check if Auth0 is configured
if not settings.AUTH0_DOMAIN:
    # Auth0 not configured - allow passthrough (Claude Code mode)
    return self.get_response(request)

# Auth0 configured but no token - return 401 with OAuth metadata
# This triggers Claude.ai's OAuth discovery flow
return self._oauth_required_response(request)
```

With:

```python
# No token present - determine behavior by request method:
# - POST: Claude Code making tool calls, allow through
# - GET: Claude.ai doing OAuth discovery, return 401 with metadata
if request.method == "POST":
    # Claude Code - allow through without auth
    return self.get_response(request)

# GET request - if Auth0 configured, trigger OAuth discovery
if settings.AUTH0_DOMAIN:
    return self._oauth_required_response(request)

# Auth0 not configured - allow all through
return self.get_response(request)
```

### Auth0 Configuration (on Auth0 Dashboard)

- **Domain:** `dev-yjnmn4q2uqphuam2.us.auth0.com`
- **Client ID:** `KDoHUD5L0xydOVJywP5f9DoByTpkeOg9`
- **API Identifier:** `https://etaverse.com/tjai/mcp` (NO trailing slash - LOCKED, cannot change)
- **Application Type:** Single Page Application (changed from Regular Web Application)
- **Signing Algorithm:** RS256

### Environment Variables (`/var/www/tjai/.env`)

```bash
AUTH0_DOMAIN=dev-yjnmn4q2uqphuam2.us.auth0.com
AUTH0_CLIENT_ID=KDoHUD5L0xydOVJywP5f9DoByTpkeOg9
AUTH0_CLIENT_SECRET=bezd8altDSwLkO0MoqR-NqaUQJarne1vZ2OL1hMrS4Mp5RUF2or3YbieasC65CfH
AUTH0_API_IDENTIFIER=https://etaverse.com/tjai/mcp
```

### Files Involved

1. **`tjai_app/middleware.py`** - Authentication routing (NEEDS THE FIX ABOVE)
2. **`tjai_app/auth0.py`** - JWT validation, has debug logging
3. **`tjai_app/views.py`** - Contains `oauth_protected_resource` view for `.well-known/oauth-protected-resource`
4. **`tjai_project/urls.py`** - URL patterns, handles both `/mcp` and `/mcp/`

### Verification Steps After Fix

1. **Test Claude Code (no auth):**
   ```bash
   curl -X POST https://etaverse.com/tjai/mcp \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
   ```
   Should return tool list, not 401.

2. **Test OAuth discovery (GET):**
   ```bash
   curl -I https://etaverse.com/tjai/mcp
   ```
   Should return 401 with `WWW-Authenticate` header.

3. **Test well-known endpoint:**
   ```bash
   curl https://etaverse.com/tjai/.well-known/oauth-protected-resource
   ```
   Should return JSON with `resource: "https://etaverse.com/tjai/mcp"` (no trailing slash).

4. **Test in Claude.ai:**
   - Settings → Connectors → Add custom connector
   - URL: `https://etaverse.com/tjai/mcp` (no trailing slash to match API identifier)

### Unresolved Issues

1. **Token validation** - Even after Auth0 login, connection fails. The auth0.py has debug logging. Check `/var/log/apache2/etaverse_ssl_error.log` for token headers.

2. **JWE vs JWT** - Auth0 was issuing JWE (encrypted) tokens with header `{'alg': 'dir', 'enc': 'A256GCM'}` instead of JWT (signed) with RS256. Changed app type to SPA but not confirmed working.

### Deployment

After fixing middleware.py:
```bash
cd /home/admin/github/tjrepo/tjai
./deploy.sh
sudo systemctl restart apache2
```
