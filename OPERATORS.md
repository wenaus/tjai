# TJ Operators Reference

This document describes all the operators implemented in the TJ (Personal AI Memory Aid) CLI tool.

## Editor Integration

### tj -e / tj --edit
Open editor for entry creation
- `tj -e` (empty editor)
- `tj --edit this is initial content` (editor with initial content)
- `tj d -e` (todo with editor)
- `tj p --edit profile content` (profile with editor)

### Bulk Editing
Launch $EDITOR for bulk entry modification (like git commit workflow)
- Export current numbered entries to temp file
- Launch $EDITOR and wait for completion  
- Parse changes and update entries
- Handle multi-line content naturally

## Calendar/Journal System

### tj j <date> <content>
Add calendar/journal entries with flexible date parsing
- `tj j 16:30 group meeting` (today with time)
- `tj j group meeting` (today without time)
- `tj j 1127 event description` (Nov 27, mmdd format)
- `tj j 20251127 event description` (Nov 27, full date)
- `tj j tomorrow event description` (tomorrow)
- `tj j mon event description` (coming Monday)
- `tj j wed event description` (coming Wednesday)

### tj j -e [date/range]
Bulk calendar editing in structured format
- `tj j -e` (edit today's calendar)
- `tj j -e week` (edit this week)
- `tj j -e 1127` (edit specific date)

Format supports nested structure:
```
20251110 Week 46
  Mon Nov 10
    [Event name](link)
    Another event
  Tue Nov 11
    [Meeting](link)
```

## Core Entry Operations

### tj . <number> <text>
Add a sub-note to an entry
- `tj . 5 follow up on this tomorrow`

### tj e <number> <text>
Edit an entry (requires confirmation)
- `tj e 3 updated content here`

### tj t <number> <tag>
Add tag to entry
- `tj t 2 urgent`

### tj t <tagname>
List all entries with a specific tag
- `tj t urgent`

### tj m <number> [context]
Move entry to context (empty context removes from context)
- `tj m 4 work`
- `tj m 4` (removes from context)

### tj s <number>
Show detailed entry information
- `tj s 7`

### tj ^ <number>
Move entry to top (updates timestamp to now)
- `tj ^ 1`

## Delete Operations

### tj x <number>
Delete an entry (requires confirmation)
- `tj x 3`

### tj x <number> t <tag>
Remove a specific tag from an entry (requires confirmation)
- `tj x 5 t urgent`

### tj x t <tagname>
Remove all instances of a tag from the system (requires confirmation)
- `tj x t obsolete`

## Listing Operations

### tj a [filter]
List all entries with optional filter
- `tj a` (all entries)
- `tj a 3` (entries from last 3 days)
- `tj a meeting` (entries containing "meeting")

### tj l c
List contexts with descriptions and entry counts
- Shows current context
- Displays: `number  context_name - entry_count - description *`
- Asterisk (*) marks current context

### tj l t
List all tags with usage counts

### tj l p/b/d
List profiles/bookmarks/todos respectively

## Context Management

### tj c <name> [description]
Create or set context with optional description
- `tj c work` (create/set work context)
- `tj c tjai "Personal AI memory system development"` (with description)
- Confirms before updating existing context descriptions

### tj c
Clear current context (requires confirmation, defaults to N)

## Features

### Numbered Operations
All displayed entries have numbers for easy operation. Every view supports numbered operations.

### Gentle Coloration
- URLs displayed in cyan (\033[96m)
- Tags displayed in light green (\033[92m)
- Optimized for dark mode terminals

### No Content Truncation
Full content displayed across all views for wide terminals.

### Confirmation Pattern
- **Requires confirmation**: Delete operations, edit operations
- **No confirmation**: Add operations (tags, sub-notes, moves)

### Context Entities
Contexts are now full entities with:
- Name
- Description
- Creation timestamp
- Modification timestamp
- Entry counts in listings

### Smart Filtering
`tj a` intelligently detects:
- Numeric input as day count filter
- Text input as content filter

All operators work with the recent entries list (last 24 hours) for numbered references.