# Next Steps

## Deploy Auth0 OAuth on etaverse.com

OAuth 2.1 integration for Claude.ai MCP connector has been implemented. Deploy as follows:

### 1. Pull latest code
```bash
cd /var/www/tjai
git pull
```

### 2. Install new dependency
```bash
source .venv/bin/activate
pip install python-jose[cryptography]
```

### 3. Add Auth0 credentials to .env
Add these lines to `/var/www/tjai/.env`:
```bash
AUTH0_DOMAIN=dev-yjnmn4q2uqphuam2.us.auth0.com
AUTH0_CLIENT_ID=KDoHUD5L0xydOVJywP5f9DoByTpkeOg9
AUTH0_CLIENT_SECRET=bezd8altDSwLkO0MoqR-NqaUQJarne1vZ2OL1hMrS4Mp5RUF2or3YbieasC65CfH
AUTH0_API_IDENTIFIER=https://etaverse.com/tjai/mcp
```

### 4. Restart the Django app
```bash
sudo systemctl restart tjai
# or however the app is managed (gunicorn, uwsgi, etc.)
```

### 5. Verify deployment
```bash
# Check well-known endpoint
curl https://etaverse.com/tjai/.well-known/oauth-protected-resource

# Should return JSON with authorization_servers pointing to Auth0
```

### 6. Test in Claude.ai
- Go to Claude.ai Settings → Connectors
- Add custom connector: `https://etaverse.com/tjai/mcp/`
- Should trigger OAuth flow through Auth0

## Implementation Notes

- **Claude Code (CLI)** continues working without auth (no token = passthrough)
- **Claude.ai (web)** requires OAuth via Auth0
- Middleware at `tjai_app/middleware.py` handles the routing
- Token validation at `tjai_app/auth0.py` uses Auth0 JWKS
