# Local Maintenance Actions

`tj_agent` can run small maintenance actions on the machine where the sync
agent is already running. These are local configuration, not TJAI `kind=action`
entries, and are not seen or scheduled by the EC2 action agent.

## Configuration

Add actions to `~/.tjai/config.json`:

```json
"local_actions": {
  "update_tjrepo": {
    "enabled": true,
    "scheduled_time": "0330",
    "command": [
      "/Users/wenaus/.tjai/venv/bin/python3",
      "-m",
      "tj_agent",
      "git-update",
      "/Users/wenaus/github/tjrepo",
      "--fetch-url",
      "git@github.com:wenaus/tjrepo.git"
    ],
    "working_directory": "/Users/wenaus/github/tjrepo/tjai",
    "timeout_seconds": 300
  }
}
```

Times use the machine's local timezone. The sync daemon checks after each sync
cycle and runs an action once per calendar day after its scheduled time. This
means a sleeping Mac runs the action after it wakes instead of permanently
missing the overnight window. Last-attempt state is stored in
`~/.tjai/local_actions_state.json`.

Restart `tj_agent` after changing local action configuration because the main
sync process caches `config.json`.

## Runner Contract

Each enabled action supplies an argument array in `command`; the runner never
passes it through a shell. `working_directory` and `timeout_seconds` are
optional. Exit zero records a local success. A nonzero exit, invalid
configuration, launch error, or timeout is posted to TJAI AppLog at `ERROR`
level with source `local-action`, so it appears red on the dashboard. Command
output is bounded before logging.

## Git Safety

`update_tjrepo` fetches the configured upstream and updates only when the local
branch can be fast-forwarded. It does not merge divergent history, stash,
discard, commit, or overwrite local work. If the checkout is behind and dirty,
the action leaves it untouched for manual reconciliation.

The optional `--fetch-url` overrides authentication only for the fetch without
changing the checkout's configured remote. Git terminal prompts are disabled,
and SSH runs in batch mode with a connection timeout, so unattended auth
problems fail promptly and reach AppLog.

Successful no-op and update results are written to `~/.tjai/agent.log`.
Blocked updates and errors exit nonzero and are posted by the local action
runner to TJAI AppLog, using `TJAI_API_KEY` from the agent environment.

Run the configured action manually with:

```bash
~/.tjai/venv/bin/python3 -m tj_agent local-action update_tjrepo
```
