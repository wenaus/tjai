"""Django bootstrap for tjai scripts.

Usage:
    import bootstrap  # must be first import
    from tjai_app.models import Entry, Tag

Works from dev (/home/admin/github/tjrepo/tjai) and prod (/var/www/tjai).
Finds venv, .env, and Django settings automatically.
If invoked with the wrong python, re-execs with the venv python.
"""
import os
import sys
from pathlib import Path

# Project root: parent of scripts/
root = Path(__file__).resolve().parent.parent

# Candidate roots in priority order (dev first, then prod)
ROOTS = [root, Path('/var/www/tjai')]

# 1. If we're not running under a venv python, re-exec with the right one.
if not hasattr(sys, 'real_prefix') and sys.prefix == sys.base_prefix:
    for r in ROOTS:
        venv_python = r / '.venv' / 'bin' / 'python3'
        if venv_python.exists():
            os.execv(str(venv_python), [str(venv_python)] + sys.argv)
    print("FATAL: No .venv found in any of:", [str(r) for r in ROOTS], file=sys.stderr)
    sys.exit(1)

# 2. Add project root to sys.path (for Django app imports)
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

# 3. Load .env
for r in ROOTS:
    env_file = r / '.env'
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
        break

# 4. Django setup
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tjai_project.settings')
import django
django.setup()
