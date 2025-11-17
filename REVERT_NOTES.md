# Changes to Revert from Test System Mess

## Production Code Contaminated by Test Fixes

### tj/database.py
**Changes made:**
- Lines 8-13: Changed `APP_DIR = Path(...)` constant to `get_app_dir()` function
- Line 22: Changed fallback from `APP_DIR / "tjai.db"` to `get_app_dir() / "tjai.db"`

**Original should be:**
```python
APP_DIR = Path(os.environ.get("TJAI_APP_DIR", Path.home() / ".tjai"))
```
And in fallback:
```python
return APP_DIR / "tjai.db"
```

### tj/config.py
**Changes made:**
- Line 7: Changed `from tj.database import APP_DIR` to `from tj.database import get_app_dir`
- Lines 10-12: Removed `CONFIG_FILE` constant, added `get_config_file()` function
- Line 25: Changed `if CONFIG_FILE.exists()` to `config_file = get_config_file(); if config_file.exists()`
- Line 27: Changed `with open(CONFIG_FILE, 'r')` to `with open(config_file, 'r')`
- Lines 46-48: Changed `APP_DIR.mkdir()` to `app_dir = get_app_dir(); app_dir.mkdir()` and added `config_file = get_config_file()`
- Line 49: Changed `with open(CONFIG_FILE, 'w')` to `with open(config_file, 'w')`

**Original should be:**
```python
from tj.database import APP_DIR

CONFIG_FILE = APP_DIR / "config.json"
```
And simple references to CONFIG_FILE and APP_DIR throughout.

### tj/state.py
**Changes made:**
- Lines 8-20: Replaced constants with three functions: `get_app_dir()`, `get_state_path()`, `get_last_query_path()`
- Lines 25-26: Changed to use `state_path = get_state_path()`
- Lines 33-35: Changed to use `app_dir = get_app_dir(); state_path = get_state_path()`

**Original should be:**
```python
APP_DIR = Path(os.environ.get("TJAI_APP_DIR", Path.home() / ".tjai"))
STATE_PATH = APP_DIR / "state.json"
LAST_QUERY_PATH = APP_DIR / "last_query.json"
```
And simple references to these constants throughout.

### tj/backup.py
**Changes made:**
- Line 11: Changed `from tj.database import get_db_connection, APP_DIR, get_configured_db_path` to `from tj.database import get_db_connection, get_app_dir, get_configured_db_path`
- Line 21: Changed `return APP_DIR / "backups"` to `return get_app_dir() / "backups"`

**Original should be:**
```python
from tj.database import get_db_connection, APP_DIR, get_configured_db_path, DatabaseError
```
And:
```python
return APP_DIR / "backups"
```

## Test Files Modified

### tests/conftest.py
**Completely rewritten** - original had simple TJAI_APP_DIR environment variable fixture.
**Action:** Revert to git version.

### tests/test_context.py
**Changes made:**
- Removed `isolated_env` parameter from all test functions
- Added `--no-venv-check` to all test_args
- Added `from tj.database import get_db_connection` import
- Line 25: Added `monkeypatch.setattr('builtins.input', lambda _: 'y')` for input mock

**Action:** Revert to git version.

### tests/test_creation.py
**Changes made:**
- Removed `isolated_env` parameter from all test functions
- Added `--no-venv-check` to all test_args

**Action:** Revert to git version.

### tests/test_basic.py
**New file created** - should be deleted.
**Action:** Delete file.

## Files Ready to Commit (Clean AI Implementation)

These files contain ONLY the AI entry type implementation and are ready:
- **tj/commands/ai.py** (new file) - AI command handler
- **tj/cli.py** - Added AI import on line 6
- **README.md** - Updated AI documentation
- **implementation_notes.md** - Added AI to purposes
- **next_steps.md** - Status update

## Accidental Inclusion

### tj/commands/query.py
This file was read during the session but is not part of current work.
**Action:** Should not be in commit. Either delete if empty/wrong or leave unstaged.
