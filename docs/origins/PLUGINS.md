# TJ Plugin Architecture

## Overview

Plugins extend tj with machine-specific commands on servers without polluting the universal interface. Plugin commands appear as flat top-level commands (e.g., `tj status`, not `tj plugin status`).

## Design Principles

1. **Plugins are optional** - tj works identically with zero plugins
2. **Flat namespace** - plugin commands are top-level tj commands
3. **No collisions** - plugins cannot override core commands
4. **Consistent logging** - all plugin operations use `log_operation()`
5. **State separation** - plugin enablement in local state, discovery records in db

## Plugin Lifecycle

### Discovery (first run on machine)

1. Check local state for `enabled_plugins`
2. If empty, query db for `plugin_enable` log entries matching current hostname
3. If match found, prompt: "Plugin 'etaverse' was enabled on this hostname. Enable? [y/N]"
4. On confirm: add to local state, log `plugin_enable` with hostname

### Loading (every tj invocation)

1. Read enabled plugins from local state
2. Import plugin module from `tj/plugins/{name}.py`
3. Call `plugin.get_commands()` to get command dict
4. Validate no collisions with core commands
5. Merge into command dispatch

### Management

```bash
tj admin plugin list              # Show available plugins, enabled status
tj admin plugin enable etaverse   # Enable plugin, log to db with hostname
tj admin plugin disable etaverse  # Disable plugin
tj admin plugin scan              # Re-check db for hostname matches
```

## File Structure

```
tj/plugins/
├── __init__.py      # Plugin loader, registration, discovery
├── base.py          # PluginBase class, PluginCommand helper
├── etaverse.py      # etaverse.com server operations
└── labserver.py     # BNL lab server (future)
```

## Plugin Module Contract

Each plugin module must define:

```python
PLUGIN_NAME = "etaverse"
PLUGIN_DESCRIPTION = "etaverse.com server operations"
PLUGIN_HOSTNAMES = ["ip-172-*", "*.etaverse.com"]  # Glob patterns

def get_commands() -> dict:
    """Return dict mapping command names to (handler, help_text, args_spec)."""
    return {
        "status": (cmd_status, "System health check", []),
        "deploy": (cmd_deploy, "Deploy application", [("app", {"help": "App name"})]),
    }

def cmd_status(args):
    """Check system status and log result."""
    ...
```

## State Storage

Local state (`~/.tjai/state.json`):
```json
{
  "current_context": "tjai",
  "plugins": {
    "etaverse": {"enabled": true, "last_status": 1732800000.0}
  },
  "plugins_discovered_at": 1732800000.0,
  "plugins_hostname": "ip-172-31-xx-xx"
}
```

Database records (kind='log', operation='plugin_enable'):
```json
{
  "plugin": "etaverse",
  "hostname": "ip-172-31-xx-xx",
  "hostname_pattern": "ip-172-*"
}
```

## Hostname Matching

Uses glob patterns via `fnmatch`:
- `ip-172-31-*` matches EC2 instances
- `etaverse.com` matches exact hostname
- `lab-*` matches lab machines

## Help Integration

`tj h` shows plugin section when plugins enabled:

```
PLUGINS (machine-specific)
  etaverse:
    tj status       System health check
    tj deploy       Deploy application
```

## Error Handling

- **Plugin load failure**: Log error, continue without plugin, warn once
- **Command collision**: Refuse to load, log error, show message
- **Missing module**: Warn, remove from enabled, continue

## Design Decisions

### Q1: Argparse subparsers or simple positional args?

**Decision: Argparse subparsers** - consistent with core commands, automatic help generation, supports nested subcommands like `tj postgres create tjai`.

### Q2: Plugin state namespace?

**Decision: Nested** - `state["plugins"]["etaverse"]["key"]` keeps plugin state isolated, easy cleanup, readable.

### Q3: Discovery timing?

**Decision: Lazy first-run** - check only on first run or hostname change, cache result. No per-command overhead.

### Q4: Hostname matching?

**Decision: Glob patterns** - `fnmatch` is user-friendly, sufficient for all cases, no regex complexity.

## Implementation Phases

1. Core infrastructure (base.py, loader, state helpers)
2. Admin commands (`tj admin plugin ...`)
3. Help integration
4. First plugin (etaverse with status command)
