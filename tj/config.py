"""Configuration management for tjai."""

import json
from pathlib import Path
from typing import Dict, Any, Optional

from tj.database import APP_DIR

CONFIG_FILE = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "db_dir": "~/Dropbox/Current",
    "backup_dir": "~/Dropbox/Current/tjai_backups",
    "backup_interval_hours": 1,
    "recent_entries_hours": 24,
    "backup_retention_days": 14,
    "calendar_default_days": 30,
    "content_truncate_length": 5,
    "status_list_limit": 20,
    "line_wrap_width": 100,
    "preview_length": 50
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


def get_location_name() -> str:
    """Get location name, prompting user if not set."""
    import socket
    config = get_config()
    if not config.get('location_name'):
        hostname = socket.gethostname()
        print(f"Location name not set. Default is hostname: {hostname}")
        response = input("Enter location name (or press Enter for default): ").strip()
        config['location_name'] = response if response else hostname
        save_config(config)
        print(f"Location set to: {config['location_name']}")
    return config['location_name']


def get_db_path() -> Path:
    """Get the location-specific database path.

    Returns path like ~/Dropbox/Current/tjai_{location_name}.db
    On first run, copies from generic tjai.db if it exists.
    """
    import shutil

    config = get_config()
    location_name = get_location_name()

    # Build location-specific path
    base_dir = Path(config.get("db_dir", "~/Dropbox/Current")).expanduser()
    location_db = base_dir / f"tjai_{location_name}.db"

    # Create parent directories if needed
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Warning: Could not create database directory: {e}")

    # Bootstrap: copy from generic tjai.db if location-specific doesn't exist
    if not location_db.exists():
        generic_db = base_dir / "tjai.db"
        if generic_db.exists():
            # Use copyfile (not copy2) - copy2 tries to preserve metadata
            # which fails on WSL2 writing to NTFS/Windows filesystems
            shutil.copyfile(generic_db, location_db)
            print(f"Copied {generic_db} to {location_db}")

    return location_db


def get_backup_dir() -> Path:
    """Get the backup directory path, expanding ~ and creating if needed."""
    config = get_config()
    backup_dir_str = config.get("backup_dir", DEFAULT_CONFIG["backup_dir"])

    expanded_path = Path(backup_dir_str).expanduser()

    try:
        expanded_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Warning: Could not create backup directory: {e}")

    return expanded_path


# Legacy alias for compatibility
def get_backup_path() -> Path:
    """Legacy alias for get_backup_dir."""
    return get_backup_dir()


def set_db_dir(new_dir: str) -> None:
    """Set a new database directory in the configuration."""
    config = get_config()
    config["db_dir"] = new_dir
    save_config(config)
    print(f"Database directory set to: {new_dir}")


def get_config_lines() -> list[str]:
    """Return configuration as formatted lines for display."""
    from tj.commands.editor import get_editor_command

    config = get_config()
    lines = []

    # Skip legacy keys
    skip_keys = {'db_path', 'backup_path'}

    for key, value in config.items():
        if key in skip_keys:
            continue
        if key == "db_dir":
            expanded = Path(value).expanduser()
            lines.append(f"  {key}: {value} -> {expanded}")
        elif key == "backup_dir":
            expanded = Path(value).expanduser()
            lines.append(f"  {key}: {value} -> {expanded}")
        else:
            lines.append(f"  {key}: {value}")

    # Add derived values
    lines.append(f"  db_path: {get_db_path()}")

    # Add editor (not stored in config, resolved dynamically)
    editor = get_editor_command()
    lines.append(f"  editor: {editor}")

    return lines


def show_config() -> None:
    """Display current configuration."""
    print("Current configuration:")
    for line in get_config_lines():
        print(line)


def _get(key: str):
    """Get config value with fallback to default."""
    return get_config().get(key, DEFAULT_CONFIG[key])


def get_backup_interval_hours() -> int:
    return _get("backup_interval_hours")


def get_recent_entries_hours() -> int:
    return _get("recent_entries_hours")


def get_backup_retention_days() -> int:
    return _get("backup_retention_days")


def get_calendar_default_days() -> int:
    return _get("calendar_default_days")


def get_content_truncate_length() -> int:
    return _get("content_truncate_length")


def get_status_list_limit() -> int:
    return _get("status_list_limit")


def get_line_wrap_width() -> int:
    return _get("line_wrap_width")


def get_preview_length() -> int:
    return _get("preview_length")


def handle_config_command(args) -> None:
    """Handle the config command."""
    if not hasattr(args, 'action') or not args.action:
        show_config()
        return

    if args.action == "show":
        show_config()
    elif args.action == "db-dir":
        if hasattr(args, 'path') and args.path:
            set_db_dir(args.path)
        else:
            config = get_config()
            current_dir = config.get("db_dir", DEFAULT_CONFIG["db_dir"])
            expanded = Path(current_dir).expanduser()
            print(f"Database directory: {current_dir} -> {expanded}")
            print(f"Database path: {get_db_path()}")
    else:
        print(f"Unknown config action: {args.action}")
        print("Available actions: show, db-dir")