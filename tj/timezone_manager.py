"""Timezone management for tjai."""

import sqlite3
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict

from tj.database import get_db_connection, DatabaseError


# Timezone mappings - use IANA standard names (America/*) not legacy US/* aliases
# US/* may not be available on all systems (e.g., WSL2)
TIMEZONE_ALIASES = {
    'eastern': 'America/New_York',
    'central': 'America/Chicago',
    'mountain': 'America/Denver',
    'pacific': 'America/Los_Angeles',
    'euro': 'Europe/London'
}

DEFAULT_TIMEZONE = 'America/New_York'

# Cache timezone to avoid repeated DB lookups
_cached_timezone: Optional[str] = None
_cached_tz_object: Optional[ZoneInfo] = None


def get_current_timezone() -> str:
    """Get the current timezone setting (cached for performance)."""
    global _cached_timezone
    if _cached_timezone is not None:
        return _cached_timezone

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT value FROM sync_metadata
            WHERE key = 'timezone'
        """)

        row = cursor.fetchone()
        _cached_timezone = row['value'] if row else DEFAULT_TIMEZONE
        return _cached_timezone

    except sqlite3.Error:
        return DEFAULT_TIMEZONE


def get_timezone_object() -> Optional[ZoneInfo]:
    """Get the current timezone as a ZoneInfo object (cached).

    Returns ZoneInfo object or None if timezone is invalid.
    """
    global _cached_tz_object
    if _cached_tz_object is not None:
        return _cached_tz_object

    tz_name = get_current_timezone()
    try:
        _cached_tz_object = ZoneInfo(tz_name)
    except Exception:
        traceback.print_exc()
        _cached_tz_object = None
    return _cached_tz_object


def set_timezone(timezone: str) -> None:
    """Save the timezone setting."""
    global _cached_timezone, _cached_tz_object
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO sync_metadata (key, value, timestamp_updated)
            VALUES ('timezone', ?, ?)
        """, (timezone, datetime.now().timestamp()))

        conn.commit()
        _cached_timezone = timezone  # Update cache
        _cached_tz_object = None  # Clear ZoneInfo cache to recompute

    except sqlite3.Error as e:
        raise DatabaseError(f"Failed to save timezone: {e}")


def parse_timezone(tz_input: str) -> Optional[str]:
    """Parse timezone input and return the full timezone name."""
    tz_input = tz_input.lower().strip()
    
    # Handle aliases
    if tz_input in TIMEZONE_ALIASES:
        return TIMEZONE_ALIASES[tz_input]
    
    # Handle UTC offsets like +5, -8
    if tz_input.startswith(('+', '-')) and tz_input[1:].isdigit():
        offset = int(tz_input)
        if -12 <= offset <= 14:  # Valid UTC offset range
            return f"Etc/GMT{-offset:+d}"  # Note: GMT zones are inverted
        return None
    
    # Handle direct timezone names (validate basic format)
    if '/' in tz_input and len(tz_input) > 3:
        return tz_input
    
    return None


def format_time_in_timezone(timestamp: float, timezone: str) -> str:
    """Format a timestamp in the specified timezone (includes date)."""
    try:
        tz = ZoneInfo(timezone)
        dt = datetime.fromtimestamp(timestamp, tz=tz)
        return dt.strftime('%m/%d %I:%M%p').lower()
    except Exception:
        traceback.print_exc()
        # Fallback for any timezone errors - use local time
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime('%m/%d %I:%M%p').lower()


def format_time_only(timestamp: float, timezone: str = None) -> str:
    """Format a timestamp as time only (no date), no leading zero on hour."""
    try:
        if timezone:
            tz = ZoneInfo(timezone)
            dt = datetime.fromtimestamp(timestamp, tz=tz)
        else:
            dt = datetime.fromtimestamp(timestamp)
        # %-I avoids leading zero on hour (Unix), lstrip('0') as fallback
        time_str = dt.strftime('%I:%M%p').lower().lstrip('0')
        return time_str
    except Exception:
        traceback.print_exc()
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime('%I:%M%p').lower().lstrip('0')


def format_time_dashboard(timestamp: float) -> str:
    """Format timestamp in dashboard style (Tue MM/DD/HH:MM)."""
    try:
        current_tz = get_current_timezone()
        tz = ZoneInfo(current_tz)
        entry_time = datetime.fromtimestamp(timestamp, tz=tz)
        return entry_time.strftime("%a %m/%d/%H:%M %Z")
    except Exception:
        traceback.print_exc()
        # Fallback on any error
        return "--- --/--/--:--"


def get_timezone_info() -> Dict[str, str]:
    """Get information about available timezones."""
    current = get_current_timezone()
    
    info = {
        'current': current,
        'aliases': TIMEZONE_ALIASES,
        'utc_example': 'Use +5 or -8 for UTC offsets'
    }
    
    return info


def handle_timezone_command(args) -> None:
    """Handle the timezone command."""
    if not hasattr(args, 'zone') or not args.zone:
        # Show current timezone and options
        info = get_timezone_info()
        print(f"Current timezone: {info['current']}")
        print("\nAvailable options:")
        for alias, full_name in info['aliases'].items():
            print(f"  {alias} -> {full_name}")
        print(f"  {info['utc_example']}")
        return
    
    # Set new timezone
    new_tz = parse_timezone(args.zone)
    if new_tz is None:
        print(f"Invalid timezone: {args.zone}")
        print("Use: eastern, central, mountain, pacific, euro, or +/-N for UTC offsets")
        return
    
    try:
        set_timezone(new_tz)
        print(f"Timezone set to: {new_tz}")
        
        # Show current time in new timezone
        now = datetime.now().timestamp()
        formatted_time = format_time_in_timezone(now, new_tz)
        print(f"Current time: {formatted_time}")
        
    except DatabaseError as e:
        print(f"Failed to set timezone: {e}")