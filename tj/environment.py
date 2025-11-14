import os
import sys
from pathlib import Path

def check_virtual_environment() -> None:
    """Check if we're running in a virtual environment and guide setup if not."""
    
    # Skip check during testing
    if os.environ.get('TJAI_APP_DIR') or 'pytest' in sys.modules:
        return
    
    # Check if we're in a virtual environment
    in_venv = (
        hasattr(sys, 'real_prefix') or  # virtualenv
        (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix) or  # venv
        os.environ.get('VIRTUAL_ENV') is not None  # environment variable
    )
    
    if in_venv:
        return  # We're good, continue
    
    # Not in venv - find the project directory and guide setup
    script_path = Path(__file__).resolve()
    project_dir = script_path.parent.parent  # Go up from tj/environment.py to tjai/
    
    print("Error: Virtual environment not detected.", file=sys.stderr)
    print(f"Please set up your virtual environment first:", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"  cd {project_dir}", file=sys.stderr)
    print(f"  python3 -m venv .venv", file=sys.stderr)
    print(f"  source .venv/bin/activate", file=sys.stderr)
    print(f"  pip install -r requirements.txt", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"Then run tj again.", file=sys.stderr)
    
    sys.exit(1)