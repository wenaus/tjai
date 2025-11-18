# Next Steps for TJ Development

## CURRENT STATUS - Session Ending Nov 17

### Test System Rewrite - INCOMPLETE (One Bug Remaining)

**What was accomplished:**
- Deleted tests/ directory (pytest-based tests completely removed)
- Removed `--no-venv-check` flag (was only used for broken pytest tests)
- Added `--test` flag to tj.py that hardwires DB path to `tjai/test.db`
- Modified `database.py` get_configured_db_path() to check `--test` in sys.argv
- Created `test.py` - round-trip test: load sample_dump.sh → dump DB → compare outputs
- Created `sample_dump.sh` - executable shell script with plain `tj` commands
- Updated `.gitignore` to exclude `test.db`

**Testing approach:**
- No mocking, no frameworks
- sample_dump.sh contains plain tj commands (e.g., `tj =work -t "Title"`)
- test.py runs it with bash alias: `alias tj='./tj.py --test'`
- Dumps database back out
- Compares for exact match (round-trip verification)

**Current bug:**
- When running `./tj.py --test dump`, the `--test` flag remains in sys.argv
- main() sees it and creates entry with content "--test dump"
- Dump output is "Created memory: --test dump" instead of actual dump
- Test fails with output mismatch

**Fix needed:**
One of these approaches:
1. Remove `--test` from sys.argv after detecting it in entrypoint()
2. Restructure to use argparse properly (parse args, check args.test, consume it)
3. Filter dump output in test.py to only lines starting with `tj ` or `#`

User rejected option 1 as "dirty hack" and didn't approve the other options before session end.

**Files modified:**
- `tj/cli.py` - Added --test flag, removed --no-venv-check, detect test mode
- `tj/database.py` - Check for --test in sys.argv, return hardwired path
- `test.py` - New test script (needs --test bug fix to work)
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
