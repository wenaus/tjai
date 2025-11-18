# Next Steps for TJ Development

## CURRENT STATUS - Session Ending Nov 17 (Second Session)

### Test System Rewrite - INCOMPLETE (Architectural Issue Discovered)

**What was accomplished:**
- Deleted tests/ directory (pytest-based tests completely removed)
- Removed `--no-venv-check` flag (was only used for broken pytest tests)
- Added `--test` flag to tj.py that hardwires DB path to `tjai/test.db`
- Modified `database.py` get_configured_db_path() to check `--test` in sys.argv
- Created `test.py` - round-trip test: load sample_dump.sh → dump DB → compare outputs
- Created `sample_dump.sh` - executable shell script with plain `tj` commands
- Updated `.gitignore` to exclude `test.db`
- Implemented general flag handling mechanism in entrypoint()

**Testing approach:**
- No mocking, no frameworks
- sample_dump.sh contains plain tj commands (e.g., `tj =work -t "Title"`)
- test.py runs it with bash alias: `alias tj='./tj.py --test'`
- Dumps database back out
- Compares for exact match (round-trip verification)

**Current architectural issue:**
The flag removal timing creates a fundamental conflict:

1. **Original bug (FIXED):** Removed flags before init_db()
   - entrypoint() removed `--test` from sys.argv at line 552
   - Then called init_db() at line 555
   - database.py checked `if '--test' in sys.argv` - already gone!
   - Result: Used production DB instead of test.db
   - **Fix applied:** Move flag removal to AFTER init_db() (now at line 554)

2. **New issue (DISCOVERED):** Flags removed before backup loses command context
   - Options affect commands and should be preserved for audit trails
   - auto_backup() should record what command triggered each backup
   - Removing flags before backup means command recording would be incomplete
   - Example: `tj --test dump` would be recorded as `tj dump` (wrong)
   - Currently auto_backup() doesn't record commands, but it SHOULD
   - This is a "bad error" - backup system ignoring the actual command

**The conflict:**
- database.py needs `--test` in sys.argv to select correct DB path
- backup system needs original sys.argv for command recording
- main() needs `--test` removed so it doesn't parse as content
- No clean ordering satisfies all three requirements

**Possible solutions:**
1. Don't remove flags from sys.argv - teach main() to ignore processed flags
2. Pass processed flags as parameters through the call chain
3. Use global/module-level state (rejected - introduces side effects)
4. Restructure with proper argparse that separates global flags from commands

**Files modified:**
- `tj/cli.py` - General flag handling, flag removal moved after init_db()
- `tj/database.py` - Check for --test in sys.argv, return hardwired path
- `test.py` - New test script (not yet tested due to architectural issue)
- `sample_dump.sh` - New sample dump file
- `.gitignore` - Added test.db

### Phase 1 Refactoring - COMPLETE

**Accomplished:**
- Reduced cli.py from 1,101 to 560 lines (49% reduction)
- Created modular command handlers:
  - `tj/commands/list.py` (161 lines) - contexts, tags, entries listing
  - `tj/commands/modify.py` (242 lines) - edit, tag, move, show, pin
  - `tj/commands/delete.py` (147 lines) - delete operations
- Moved `get_entry_from_recent_list()` to `tj/commands/common.py`
- All commands tested and working
- Code is now organized by domain for easier maintenance

**Status:** Committed in 175864b

### Dump Command Implementation - COMPLETE

**Accomplished:**
- `tj dump` outputs entire database as executable tj commands
- Contexts output first with -t/-d flags
- Entries in ROWID order (actual insertion order)
- Multi-line entries use heredoc format
- Tags preserved inline with :tag notation
- Timestamps preserved via at=YYYYMMDD/HH:MM
- Heredoc syntax fixed: `at=` now comes BEFORE `<<!` (intuitive order)

**Status:** Committed in 33ca4ea

## Immediate Next Steps

1. **Fix --test flag bug** (choose and implement one approach)
2. **Verify test.py works** (should show exact round-trip match)
3. **Test with larger dump files** to measure performance
4. **Commit test system rewrite**

## Future Implementation Priorities

### 1. Editor Integration System
- `tj -e`: Empty editor for entry creation
- `tj --edit`: Editor with initial content
- Bulk editing workflow (like git commit)

### 2. Calendar/Journal System
- `tj j 16:30 meeting` (today with time)
- `tj j tomorrow event`, `tj j mon event` (relative dates)
- Flexible date parsing

### 3. Lists and Sub-notes
- Sub-notes: `tj . content` (hierarchical, 1 level max)
- Lists: `tj + item` (JSON within entry)

### 4. Link Parsing
- `//link` notation - extract canonical reference links

## Architecture Notes

**Available notation characters:** `_ ^ / + .`
- `=` contexts
- `:` tags
- `@` places
- `//` links
- `+` lists (planned)
- `.` sub-notes (planned)

**Key documentation:**
- `implementation_notes.md` - Testing philosophy: "No mocking. Meaningful tests on full function system."
- `OPERATORS.md` - Operator reference
- `CLAUDE.md` - Critical rules including git push requirement

## Commit Strategy

**CRITICAL:** Always push immediately after committing. Weekend work loss must not repeat.
