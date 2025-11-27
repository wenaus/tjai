"""Configuration management for tjai."""

import json
from pathlib import Path
from typing import Dict, Any, Optional

from tj.database import APP_DIR

CONFIG_FILE = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "db_path": "~/Dropbox/Current/tjai.db",
    "backup_path": "~/Dropbox/Current/tjai_backups",
    "backup_interval_hours": 1,
    "recent_entries_hours": 24,
    "backup_retention_days": 14,
    "calendar_default_days": 30,
    "content_truncate_length": 5
}

# Cached config (loaded once per session)
_cached_config: Optional[Dict[str, Any]] = None


def get_config() -> Dict[str, Any]:
    """Load configuration from config file (cached)."""
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                # Merge with defaults for any missing keys
                for key, value in DEFAULT_CONFIG.items():
                    if key not in config:
                        config[key] = value
                _cached_config = config
                return _cached_config
        else:
            # Create config file with defaults
            save_config(DEFAULT_CONFIG)
            _cached_config = DEFAULT_CONFIG.copy()
            return _cached_config
    except Exception:
        # Fallback to defaults if config is corrupted
        _cached_config = DEFAULT_CONFIG.copy()
        return _cached_config


def save_config(config: Dict[str, Any]) -> None:
    """Save configuration to config file."""
    global _cached_config
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        _cached_config = config.copy()  # Update cache
    except Exception as e:
        print(f"Warning: Could not save config: {e}")


def get_db_path() -> Path:
    """Get the configured database path, expanding ~ and creating parent dirs."""
    config = get_config()
    db_path_str = config.get("db_path", DEFAULT_CONFIG["db_path"])
    
    # Expand ~ to home directory
    expanded_path = Path(db_path_str).expanduser()
    
    # Create parent directories if they don't exist
    try:
        expanded_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Warning: Could not create database directory: {e}")
    
    return expanded_path


def get_backup_path() -> Path:
    """Get the configured backup path, expanding ~ and creating parent dirs."""
    config = get_config()
    backup_path_str = config.get("backup_path", DEFAULT_CONFIG["backup_path"])
    
    # Expand ~ to home directory
    expanded_path = Path(backup_path_str).expanduser()
    
    # Create backup directory if it doesn't exist
    try:
        expanded_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Warning: Could not create backup directory: {e}")
    
    return expanded_path


def set_db_path(new_path: str) -> None:
    """Set a new database path in the configuration."""
    config = get_config()
    config["db_path"] = new_path
    save_config(config)
    print(f"Database path set to: {new_path}")


def show_config() -> None:
    """Display current configuration."""
    config = get_config()
    print("Current configuration:")
    for key, value in config.items():
        if key == "db_path":
            expanded = Path(value).expanduser()
            print(f"  {key}: {value} -> {expanded}")
        else:
            print(f"  {key}: {value}")


def get_backup_interval_hours() -> int:
    """Get the backup interval in hours."""
    config = get_config()
    return config.get("backup_interval_hours", DEFAULT_CONFIG["backup_interval_hours"])


def get_recent_entries_hours() -> int:
    """Get the recent entries window in hours."""
    config = get_config()
    return config.get("recent_entries_hours", DEFAULT_CONFIG["recent_entries_hours"])


def get_backup_retention_days() -> int:
    """Get the backup retention period in days."""
    config = get_config()
    return config.get("backup_retention_days", DEFAULT_CONFIG["backup_retention_days"])


def get_calendar_default_days() -> int:
    """Get the default number of days to show in calendar view."""
    config = get_config()
    return config.get("calendar_default_days", DEFAULT_CONFIG["calendar_default_days"])


def get_content_truncate_length() -> int:
    """Get the content truncation length for list and calendar views."""
    config = get_config()
    return config.get("content_truncate_length", DEFAULT_CONFIG["content_truncate_length"])


def handle_config_command(args) -> None:
    """Handle the config command."""
    if not hasattr(args, 'action') or not args.action:
        show_config()
        return

    if args.action == "show":
        show_config()
    elif args.action == "db-path":
        if hasattr(args, 'path') and args.path:
            set_db_path(args.path)
        else:
            config = get_config()
            current_path = config.get("db_path", DEFAULT_CONFIG["db_path"])
            expanded = Path(current_path).expanduser()
            print(f"Current database path: {current_path} -> {expanded}")
    else:
        print(f"Unknown config action: {args.action}")
        print("Available actions: show, db-path")