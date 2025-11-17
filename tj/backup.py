"""Backup system for data protection."""

import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
from collections import defaultdict

from tj.database import get_db_connection, APP_DIR, get_configured_db_path, DatabaseError


def get_backup_dir():
    """Get the configured backup directory."""
    try:
        from tj.config import get_backup_path
        return get_backup_path()
    except ImportError:
        # Fallback if config module isn't available
        return APP_DIR / "backups"


def get_backup_interval_hours():
    """Get the configured backup interval in hours."""
    try:
        from tj.config import get_backup_interval_hours as config_get_interval
        return config_get_interval()
    except ImportError:
        # Fallback if config module isn't available
        return 1


def get_last_backup_time() -> Optional[float]:
    """Get the timestamp of the last backup from the database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT value FROM sync_metadata 
            WHERE key = 'last_backup_time'
        """)
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return float(row['value'])
        return None
        
    except (sqlite3.Error, ValueError):
        return None


def set_last_backup_time(timestamp: float) -> None:
    """Save the timestamp of the last backup to the database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO sync_metadata (key, value, timestamp_updated)
            VALUES ('last_backup_time', ?, ?)
        """, (str(timestamp), timestamp))
        
        conn.commit()
        conn.close()
        
    except sqlite3.Error as e:
        raise DatabaseError(f"Failed to update backup timestamp: {e}")


def needs_backup() -> bool:
    """Check if a backup is needed based on the interval."""
    last_backup = get_last_backup_time()
    if last_backup is None:
        return True

    now = datetime.now().timestamp()
    hours_since_backup = (now - last_backup) / 3600
    backup_interval = get_backup_interval_hours()

    return hours_since_backup >= backup_interval


def cleanup_old_backups() -> None:
    """Clean up old backups according to retention policy.

    Retention policy:
    - Today: Keep all hourly backups (no cleanup)
    - Yesterday+: Keep only the latest backup from each day
    - After N days: Keep only one backup per week (N from config)
    """
    try:
        backups = list_backups()
        if len(backups) <= 1:
            return  # Nothing to clean up

        now = datetime.now()
        today = now.date()

        # Get retention days from config
        try:
            from tj.config import get_backup_retention_days
            retention_days = get_backup_retention_days()
        except ImportError:
            retention_days = 7  # Fallback

        retention_cutoff = now - timedelta(days=retention_days)
        
        # Group backups by date
        backups_by_date = defaultdict(list)
        for backup in backups:
            backup_date = backup['modified'].date()
            backups_by_date[backup_date].append(backup)
        
        files_to_delete = []
        
        # Process each date
        for backup_date, date_backups in backups_by_date.items():
            
            if backup_date == today:
                # Today: Keep ALL hourly backups, don't purge anything
                continue
            elif backup_date >= retention_cutoff.date():
                # Recent days (yesterday through retention period): Keep only latest per day
                date_backups.sort(key=lambda x: x['modified'], reverse=True)
                files_to_delete.extend(date_backups[1:])  # Delete all but the latest
            else:
                # Older than 1 week: Group by week, keep only latest per week
                weeks = defaultdict(list)
                for backup in date_backups:
                    # Get Monday of the week for grouping
                    backup_monday = backup['modified'] - timedelta(days=backup['modified'].weekday())
                    week_key = backup_monday.strftime('%Y-W%W')
                    weeks[week_key].append(backup)
                
                # For each week, keep only the latest backup
                for week_backups in weeks.values():
                    week_backups.sort(key=lambda x: x['modified'], reverse=True)
                    files_to_delete.extend(week_backups[1:])  # Delete all but the latest
        
        # Delete the identified files
        deleted_count = 0
        for backup_info in files_to_delete:
            try:
                backup_info['path'].unlink()
                deleted_count += 1
            except Exception as e:
                print(f"Warning: Failed to delete backup {backup_info['filename']}: {e}")
        
        if deleted_count > 0:
            print(f"Cleaned up {deleted_count} old backup(s)")
                
    except Exception as e:
        print(f"Warning: Backup cleanup failed: {e}")


def create_backup() -> bool:
    """Create a backup of the database."""
    try:
        # Get configured backup directory
        backup_dir = get_backup_dir()
        
        # Generate backup filename with date (YYYYMMDD format)
        date_str = datetime.now().strftime("%Y%m%d")
        backup_filename = f"tjai_backup_{date_str}.db"
        backup_path = backup_dir / backup_filename
        
        print("Backing up...")
        
        # Copy the database file
        db_path = get_configured_db_path()
        if db_path.exists():
            shutil.copy2(db_path, backup_path)
            
            # Update last backup time
            now = datetime.now().timestamp()
            set_last_backup_time(now)
            
            # Clean up old backups after successful backup
            cleanup_old_backups()
            
            return True
        else:
            print("Warning: No database file to backup")
            return False
            
    except Exception as e:
        print(f"Backup failed: {e}")
        return False


def auto_backup() -> None:
    """Automatically backup if needed (called on every command)."""
    try:
        if needs_backup():
            success = create_backup()
            if not success:
                print("Warning: Backup failed")
    except Exception as e:
        # Don't let backup failures break the main command
        print(f"Warning: Backup check failed: {e}")


def list_backups() -> list:
    """List available backup files."""
    backup_dir = get_backup_dir()
    if not backup_dir.exists():
        return []
    
    backups = []
    for backup_file in backup_dir.glob("tjai_backup_*.db"):
        stat = backup_file.stat()
        backups.append({
            'filename': backup_file.name,
            'path': backup_file,
            'size': stat.st_size,
            'modified': datetime.fromtimestamp(stat.st_mtime)
        })
    
    # Sort by modification time, newest first
    backups.sort(key=lambda x: x['modified'], reverse=True)
    return backups


def restore_backup(backup_filename: str) -> bool:
    """Restore from a specific backup file."""
    backup_dir = get_backup_dir()
    backup_path = backup_dir / backup_filename
    
    if not backup_path.exists():
        print(f"Backup file not found: {backup_filename}")
        return False
    
    try:
        # Create a backup of current database before restoring
        db_path = get_configured_db_path()
        backup_dir = get_backup_dir()
        current_backup = backup_dir / f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        if db_path.exists():
            shutil.copy2(db_path, current_backup)
            print(f"Current database backed up to: {current_backup.name}")
        
        # Restore the backup
        shutil.copy2(backup_path, db_path)
        print(f"Database restored from: {backup_filename}")
        return True
        
    except Exception as e:
        print(f"Restore failed: {e}")
        return False