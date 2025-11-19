"""Timezone management for tjai."""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict

from tj.database import get_db_connection, DatabaseError


# Timezone mappings
TIMEZONE_ALIASES = {
    'eastern': 'US/Eastern',
    'central': 'US/Central', 
    'mountain': 'US/Mountain',
    'pacific': 'US/Pacific',
    'euro': 'Europe/London'
}

DEFAULT_TIMEZONE = 'US/Eastern'


def get_current_timezone() -> str:
    """Get the current timezone setting."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT value FROM sync_metadata 
            WHERE key = 'timezone'
        """)
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return row['value']
        return DEFAULT_TIMEZONE
        
    except sqlite3.Error:
        return DEFAULT_TIMEZONE


def set_timezone(timezone: str) -> None:
    """Save the timezone setting."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO sync_metadata (key, value, timestamp_updated)
            VALUES ('timezone', ?, ?)
        """, (timezone, datetime.now().timestamp()))
        
        conn.commit()
        conn.close()
        
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
    """Format a timestamp in the specified timezone."""
    try:
        tz = ZoneInfo(timezone)
        dt = datetime.fromtimestamp(timestamp, tz=tz)
        return dt.strftime('%m/%d %I:%M%p').lower()
    except Exception:
        # Fallback for any timezone errors - use local time
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime('%m/%d %I:%M%p').lower()


def format_time_dashboard(timestamp: float) -> str:
    """Format timestamp in dashboard style (MM/DD/HH:MM)."""
    try:
        current_tz = get_current_timezone()
        tz = ZoneInfo(current_tz)
        entry_time = datetime.fromtimestamp(timestamp, tz=tz)
        return entry_time.strftime("%m/%d/%H:%M")
    except Exception:
        # Fallback on any error
        return "--/--/--:--"


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