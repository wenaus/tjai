# TJ Design Notes

This document captures design decisions, conventions, and architectural choices made during development discussions.

## Notation Conventions

### Established Patterns
- **Tags**: `:tagname` (light green coloring in terminal) - using `:` because `#` gets interpreted as comments in bash
- **Places**: `@place` (using @ because "at" has direct semantic meaning - "at the office", "at home")
- **NOTE**: Use `:tagname` consistently throughout codebase and docs (not `#tags`)

### Context System - Beautiful Consistency
**Decided**: Use `=` for all context operations
- **Set context**: `tj =work` 
- **Clear context**: `tj c` (general clear command) or `tj =0` (zero as clear)
- **Inline context**: `tj =work some content`, `tj d =work task`
- **Rationale**: Consistent symbol, no bash conflicts without spaces, clean and intuitive

### Reference Link Convention
**Decided**: Use `//` prefix for canonical reference links
- **Parsing**: `//link` is parsed out and stored as THE reference for that entry
- **Universal**: Works for contexts, entries, todos - everything
- **Separation**: Content and reference link are stored separately
- **Additional URLs**: Entry content can still contain other URLs in markdown or naked form

Examples:
```bash
tj =work Project planning //https://docs.google.com/document/d/abc123
tj meeting notes from today //https://docs.google.com/document/d/meeting123
tj d finish presentation //https://docs.google.com/presentation/d/slides456
tj =0                       # Alternative clear context
```

**Rationale**: Every entry gets THE canonical reference document while supporting other URLs in content. `//` suggests URL scheme, no bash conflicts, visually distinct.

## Editor Integration Design

### Core Principle
Follow git's pattern - create temp file, launch $EDITOR, wait for completion, process result.

### Use Cases
1. **Single entry creation**: `tj -e` opens empty editor
2. **Entry with initial content**: `tj --edit initial content here` 
3. **Multi-line entries**: Natural solution for complex content
4. **Bulk editing**: Export numbered entries → edit → import changes

### State Management
- Numbered entry mapping stored in state during editor session
- Edits must complete before other tj activity in same terminal session
- State invalidated after import or other operations

## Calendar/Journal System

### Command Pattern
`tj j` for journal/calendar entries with flexible date parsing

### Date Formats Supported
- `tj j 16:30 meeting` (today with time)
- `tj j meeting` (today without time) 
- `tj j 1127 event` (mmdd format)
- `tj j 20251127 event` (full date)
- `tj j tomorrow event` (relative)
- `tj j mon event` (coming Monday)

### Bulk Calendar Editing
- `tj j -e` edit today
- `tj j -e week` edit this week
- `tj j -e 1127` edit specific date

### Calendar Structure Format
Nested structure supporting:
```
20251110 Week 46
  Mon Nov 10
    [Event name](link)
    Another event
  Tue Nov 11
    [Meeting](link)
```

## Memory and Context Issues

### Critical Insight
This project is about solving digital memory problems, yet limited conversation context creates the same memory loss we're trying to solve. Need better documentation discipline during design discussions.

### Documentation Principle
Design decisions should be captured persistently in codebase files immediately when made, not just held in conversational memory.

### Weekend Work Loss Incident
- Friday: Major operator system implementation completed but NOT pushed to remote
- Weekend: User unable to access work from home, had to do other projects
- Monday: Work finally available after git pull/merge
- **Lesson**: Always push commits immediately, especially when user says "I have to go"
- User has `implementation_notes.md` with comprehensive design decisions - was also trapped over weekend

### Related Files
- `implementation_notes.md`: User's comprehensive design and architectural decisions
- This `NOTES.md`: Session-specific design discussions and decisions
- Both should be kept synchronized and referenced during development

## AI as Entry Type
**Decided**: Add `ai` as dedicated entry type (breaks single-char convention for good cause)
- **Purpose**: Store context-dependent AI behavioral guidelines and instructions
- **Usage**: `tj ai context-specific instructions`, `tj ai =work use formal tone`
- **Programmatic access**: AIs can query `tj q ai =context` for relevant guidance
- **Rationale**: AI guidance will be foundational to the app, worthy of dedicated type

Examples:
```bash
tj ai context-specific instructions for coding work
tj ai =work use formal tone and focus on deliverables
tj ai =personal be casual and creative  
tj l ai                    # List all AI guidance entries
```

## Loose Ends to Address

### Architecture Concepts
- **Terminal sessions** - mentioned twice as important for broader architecture implications
- **AIs as direct users** - programmatic access patterns beyond just `ai` entry type
- **Lists vs Sub-notes distinction**:
  - Sub-notes: Creates new entries one level down (hierarchy limit: 1 level)
    - `tj top level item`
    - `tj . second level 1` 
    - `tj . second level 2`
  - Lists: `tj + item` adds to JSON within current list entry
    - `tj shopping list`
    - `tj + milk` 
    - `tj + bread`

### Lost Design Discussion
- **Lists implementation** - extensive discussion occurred but was not documented
- Need to reconstruct: how lists work within entry JSON, syntax, management

### Available Notation
- Reserved characters from research: `_ ^ / + .` (available for future features)
- `=` used for contexts, others remain available

## Terminal Sessions Reminder
User mentioned terminal sessions - needs follow-up discussion on broader architecture implications.