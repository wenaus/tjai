# CLAUDE.md - tjai Project Guidelines

## ⚠️ CRITICAL RULE - PRODUCTION DATABASE PROTECTION ⚠️

**The tjai production database is NOW IN ACTIVE USE.**

You may ONLY delete the database if:
1. The user explicitly asks you to delete the database, AND
2. The user confirms they want the database deleted

For testing that involves DB mods use `--db=/tmp/test.db`.

**NEVER suggest or propose deleting the production database.**

The database content must be respected and preserved at all times.

## Project Precepts

- **Conciseness:** No AI flab in code, docs, or communication
- **Shell:** Bash only. Use `~/.bashrc`. Never reference zsh
- **Minimal scope:** Do only what is asked. No scope expansion, no unrequested refactoring or "improvements."
- **Fix, don't hide:** Fix problems properly. No silent failures, no error-tolerant buggy code, no workarounds that mask issues.
- **Use MCP:** Prefer tjai MCP tools over CLI commands. If MCP is missing needed functionality, flag it for improvement.
- **Conserve context:** Context exhaustion destroys workflow. Proactively use subagents (Task tool) for: codebase exploration, bug/issue investigation, multi-file searches, research before implementation, understanding existing patterns. Keep main thread for: user interaction, decisions, approvals, actual edits, commits.
- **Subagent parallelism:** When spawning research/exploration subagents, use run_in_background=true to continue discussion with user while agent works. Check results later with Read tool on output_file.

## Commands

- **Deploy:** `./deploy/update_from_dev.sh` (rsyncs to /var/www/tjai/, installs requirements, runs migrations)
