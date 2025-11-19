# CLAUDE.md - tjai Project Guidelines

## ⚠️ CRITICAL RULE - PRODUCTION DATABASE PROTECTION ⚠️

**The tjai production database is NOW IN ACTIVE USE.**

You may ONLY delete the database if:
1. The user explicitly asks you to delete the database, AND
2. The user confirms they want the database deleted

For all testing, use `--db=/tmp/test.db` or similar test databases.

**NEVER suggest or propose deleting the production database.**

The database content must be respected and preserved at all times.

## Project Precepts

- **Conciseness:** No AI flab in code, docs, or communication
- **Shell:** Bash only. Use `~/.bashrc`. Never reference zsh
