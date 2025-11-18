# Next Steps for TJ Development

## CURRENT STATUS - Session Ending Nov 18

### Test System Implementation - NEARLY COMPLETE (1 Issue Remaining)

**What was accomplished this session:**
- Replaced `--test` flag with `--db=path` option (cleaner, explicit)
  - `--db=` appears in dump output, making dumps self-contained
  - Database path now specified explicitly in every command
- Implemented `original_command = sys.argv.copy()` for audit/backup
- Fixed multi-line entry format for dump:
  - Changed from `tj <<!` to `tj "$(cat <<'END'...END)"`
  - Reason: bash consumes `<<!` heredoc when sourcing scripts
  - New format works correctly when sourced
- Fixed inline context with timestamps: `tj =work at=20250101/09:00 content` now parses `at=` correctly
- Dump includes `--db=` on all commands
- Dump quotes context titles/descriptions
- Added blank line at end of dump output

**Files modified:**
- `tj/cli.py` - Replaced `--test` with `--db=`, added `original_command` save, fixed inline context timestamp parsing
- `tj/database.py` - Check for `--db=` in sys.argv, cache at module load
- `tj/commands/dump.py` - Multi-line format changed, `--db=` prefix added, quote titles/descriptions
- `test.py` - Round-trip test (load → dump → compare)
- `sample_dump.sh` - Test data file
- `README.md` - Added note about venv persistence for AI assistants

**Current issue:**
- Test failing because `sample_dump.sh` doesn't match actual database contents
- Entries have wrong contexts (some missing `=work`)
- Solution: Regenerate `sample_dump.sh` from actual dump of clean test database

**How to fix:**
```bash
rm test.db
# Manually create clean test data
./tj.py --db=test.db =work -t "Work Projects"
./tj.py --db=test.db =personal
./tj.py --db=test.db =work at=20250101/09:00 First work entry :project :urgent
./tj.py --db=test.db =personal at=20250101/10:00 "$(cat <<'END'
This is a multi-line entry
with several lines of content
testing heredoc format
END
)"
./tj.py --db=test.db d =work at=20250101/11:00 Complete testing
./tj.py --db=test.db p =work at=20250102/14:00 I prefer simple solutions :philosophy
./tj.py --db=test.db =work at=20250103/15:00 General memory entry
# Dump and save
./tj.py --db=test.db dump > sample_dump.sh
chmod +x sample_dump.sh
# Now test should pass
python3 test.py
```

### Phase 1 Refactoring - COMPLETE

**Accomplished:**
- Reduced cli.py from 1,101 to 571 lines (48% reduction)
- Created modular command handlers:
  - `tj/commands/list.py` (161 lines) - contexts, tags, entries listing
  - `tj/commands/modify.py` (242 lines) - edit, tag, move, show, pin
  - `tj/commands/delete.py` (147 lines) - delete operations
- Moved `get_entry_from_recent_list()` to `tj/commands/common.py`
- All commands tested and working

**Status:** Committed in 175864b

### Dump Command Implementation - COMPLETE

**Accomplished:**
- `tj dump` outputs entire database as executable tj commands
- Contexts output first with -t/-d flags (quoted)
- Entries in ROWID order (actual insertion order)
- Multi-line entries use `"$(cat <<'END'...END)"` format (works when sourced)
- Tags preserved inline with :tag notation
- Timestamps preserved via at=YYYYMMDD/HH:MM
- `--db=` prefix on all commands for self-contained dumps

**Status:** Committed in 33ca4ea

## Immediate Next Steps

1. **Fix sample_dump.sh** - Regenerate from clean test database dump (see instructions above)
2. **Verify test.py passes** - Should show exact round-trip match
3. **Test with larger dump files** to measure performance
4. **Commit test system** with push

## Future Implementation Priorities

### 1. Editor Integration System
- `tj -e`: Empty editor for entry creation (multi-line via tempfile)
- `tj --edit <n>`: Edit existing entry in editor
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
- `README.md` - User docs, now includes AI assistant guidance on venv

## Commit Strategy

**CRITICAL:** Always push immediately after committing. Weekend work loss must not repeat.
