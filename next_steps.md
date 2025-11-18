### 1. Alias Auto-Quoting Enhancement
**Problem:** Bash interprets special chars in unquoted args (`[`, `]`, `()`, etc.)
**Solution:** Define alias that auto-quotes: `alias tj='tj.py "$*"'`
**Impact:** User never needs quotes, saves typing errors
**Implementation:**
- Manual flag extraction before argparse (regex for `--db=`, `--no-venv-check`)
- Join remaining args as single content string
- Parse content with current metadata extraction logic
**Trade-off:** More work for developer, huge UX improvement for user

## Commit Strategy

**CRITICAL:** Always push immediately after committing. Weekend work loss must not repeat.
