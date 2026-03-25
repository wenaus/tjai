#!/bin/bash
# Test that claude -p can use tjai MCP to write to an entry.
# This is what the research agent actually does.
cd /var/www/tjai

MARKER="MCP_TEST_$(date +%s)"

claude -p "Call mcp__tjai__create_entry with content='$MARKER', kind='memory', context='tjai', tags=['test']. Return the entry id." \
  --system-prompt "Use mcp__tjai__ tools. Create the entry now. Return only the id." \
  --model haiku \
  --max-turns 3 \
  < /dev/null >/dev/null 2>&1

# Check if the entry was created by querying the DB directly
cd /home/admin/github/tjrepo/tjai/scripts
../.venv/bin/python << PYEOF
import bootstrap
from tjai_app.models import Entry
e = Entry.objects.filter(content='$MARKER', deleted_at__isnull=True).first()
if e:
    print("PASS: MCP works. Entry created: " + str(e.id))
    # Clean up test entry
    import time
    e.deleted_at = time.time()
    e.save(update_fields=['deleted_at'])
    print("  (test entry cleaned up)")
else:
    print("FAIL: No entry with marker '$MARKER' found. MCP is broken.")
PYEOF
