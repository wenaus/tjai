# Next Steps

## TODO: Fix MCP endpoint 404

MCP service for tjai is partially implemented but endpoint returns 404.

**What's done:**
- django-mcp-server added to requirements/base.txt
- mcp_server added to INSTALLED_APPS in settings/base.py
- DJANGO_MCP_GLOBAL_SERVER_CONFIG configured in settings
- URL route added: `path("mcp/", include("mcp_server.urls"))`
- tjai_app/mcp.py created with tools: get_calendar, get_profile, get_ai_guidance, list_contexts, create_entry, get_todos, search_entries

**The problem:**
MCP endpoint at https://etaverse.com/tjai/mcp/ returns 404.

**Likely fix:**
The mcp.py module needs to be imported for @mcp.tool() decorators to register tools. Update tjai_app/apps.py to import mcp in the ready() method:

```python
from django.apps import AppConfig

class TjaiAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tjai_app'

    def ready(self):
        from . import mcp  # Import to register MCP tools
```

**Reference:**
~/github/swf-monitor has working MCP implementation using same django-mcp-server package.

