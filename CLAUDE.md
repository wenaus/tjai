# tjai Project Guidelines

General precepts are in tjai AI guidance. Load with `get_ai_guidance(context="tjai")`.

## Production Database

The production database is in active use. For testing that involves DB mods use `--db=/tmp/test.db`. NEVER delete the production database without explicit request AND confirmation.

## Project-Specific

- **Shell:** Bash only. Use `~/.bashrc`. Never reference zsh.
- **Deploy:** `./deploy/update_from_dev.sh` (rsyncs to /var/www/tjai/, installs requirements, runs migrations)
