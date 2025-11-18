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

## Commit Strategy

**CRITICAL:** Always push immediately after committing. Weekend work loss must not repeat.
