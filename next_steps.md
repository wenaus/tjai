# Next Steps for TJ Development

## Immediate Implementation Priority

### 1. Implement New Notation System
- **=context notation**: `tj =work`, `tj =work content`, `tj =0` (clear)
- **//link parsing**: Extract and store canonical reference links separately from content
- **ai entry type**: `tj ai behavioral guidance content`

### 2. Editor Integration System
- **tj -e**: Empty editor for entry creation
- **tj --edit**: Editor with initial content
- **Bulk editing**: Export numbered entries → launch $EDITOR → parse changes → update entries
- **State management**: Store entry mappings during editor sessions

### 3. Calendar/Journal System
- **tj j**: Journal entries with flexible date parsing
  - `tj j 16:30 meeting` (today with time)
  - `tj j meeting` (today without time)  
  - `tj j 1127 event` (mmdd), `tj j 20251127 event` (full date)
  - `tj j tomorrow event`, `tj j mon event` (relative/day names)
- **tj j -e**: Bulk calendar editing with structured format

### 4. Lists and Sub-notes Implementation
- **Sub-notes**: `tj . content` creates hierarchical entries (1 level max)
  - Pattern: `tj top level item` → `tj . second level 1` → `tj . second level 2`
- **Lists**: `tj + item` adds to JSON within current list entry
  - Pattern: `tj shopping list` → `tj + milk` → `tj + bread`

## Architecture Topics to Address

### Terminal Sessions
User mentioned this twice as critical for broader architecture. Need to discuss:
- Session boundaries and state management
- Cross-session data persistence
- Multi-terminal coordination

### AIs as Direct Users
Beyond just `ai` entry type, need programmatic access patterns:
- API for AIs to query context-specific guidance
- Behavioral instruction retrieval based on current context
- Integration with external AI systems

### Lost Design Discussion
Major lists implementation discussion occurred but wasn't documented. Need to reconstruct:
- How lists work within entry JSON structure
- List management operations
- Display and editing of list contents

## Implementation Notes

### Current Architecture Status
- Comprehensive operator system implemented and working
- Context entities with descriptions functional
- Database schema supports contexts table
- Coloration system implemented (cyan URLs, green tags)
- No content truncation across all views

### Available Notation Characters
From weekend research: `_ ^ / + .` remain available for future features
- `=` used for contexts
- `:` used for tags  
- `@` used for places
- `//` used for links
- `+` designated for lists
- `.` designated for sub-notes

### Key Files to Reference
- `implementation_notes.md`: User's comprehensive architectural decisions
- `NOTES.md`: Session-specific design discussions and decisions  
- `OPERATORS.md`: Current operator documentation (needs updating with new features)

## Testing Strategy
- Test new notation parsing in all entry creation commands
- Verify editor integration workflow matches git-style experience
- Test date parsing for all supported formats
- Validate hierarchy limits for sub-notes
- Test list JSON structure and operations

## Documentation Updates Needed
- Update OPERATORS.md with new notation and commands
- Document editor integration workflow
- Add calendar/journal system documentation
- Clarify lists vs sub-notes distinction with examples

## Commit Strategy
Remember to push commits immediately, especially when user indicates session ending. The weekend work loss incident must not repeat - all work must be available across locations.