#!/usr/bin/env python3
"""Backup tjai server data, push to Dropbox via rclone.

Creates a dated directory under ~/tjai-backups/server/YYYY-MM-DD/
containing:
  - tjai-db.sql.gz   PostgreSQL database dump (compressed)
  - env-www.env      /var/www/tjai/.env
  - env-home.env     ~/.env
  - data/            /var/www/tjai/data/ (history files etc.)
  - etaverse.conf    Apache site configuration

Usage:
    python backup.py              # today's backup
    python backup.py 2026-02-22   # specific date
"""
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LOCAL_BACKUP_DIR = Path.home() / 'tjai-backups' / 'server'
RCLONE_DEST = 'dropbox:tjai-backups/server'
TJAI_WWW = Path('/var/www/tjai')
APACHE_CONF = Path('/etc/apache2/sites-enabled/etaverse.conf')

DB_NAME = 'tjai'
DB_USER = 'tjai'
DB_HOST = 'localhost'


def get_db_password():
    """Read DB password from production .env."""
    env_path = TJAI_WWW / '.env'
    if not env_path.exists():
        print(f"ERROR: {env_path} not found", file=sys.stderr)
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if 'DATABASE_URL=' in line:
            # postgres://user:pass@host:port/db
            try:
                return line.split('://')[1].split(':')[1].split('@')[0]
            except (IndexError, ValueError):
                pass
        if line.startswith('DB_PASSWORD=') or line.startswith('PGPASSWORD='):
            return line.split('=', 1)[1].strip().strip("'\"")
    print("ERROR: Could not find DB password in .env", file=sys.stderr)
    return None


def backup_postgres(backup_dir):
    """pg_dump the tjai database, gzipped."""
    dest = backup_dir / 'tjai-db.sql.gz'
    password = get_db_password()
    if not password:
        return False

    env = os.environ.copy()
    env['PGPASSWORD'] = password

    try:
        dump = subprocess.run(
            ['pg_dump', '-U', DB_USER, '-h', DB_HOST, DB_NAME],
            capture_output=True, env=env, timeout=120,
        )
        if dump.returncode != 0:
            print(f"ERROR: pg_dump failed: {dump.stderr.decode()}", file=sys.stderr)
            return False

        import gzip
        with gzip.open(dest, 'wb') as f:
            f.write(dump.stdout)

        size_mb = dest.stat().st_size / (1024 * 1024)
        print(f"  DB dump: {dest.name} ({size_mb:.1f} MB)")
        return True
    except subprocess.TimeoutExpired:
        print("ERROR: pg_dump timed out after 120s", file=sys.stderr)
        return False


def copy_file(src, backup_dir, dest_name):
    """Copy a single file to the backup directory."""
    src = Path(src)
    if not src.exists():
        print(f"  SKIP: {src} not found")
        return False
    dest = backup_dir / dest_name
    shutil.copy2(src, dest)
    print(f"  Copied: {dest_name}")
    return True


def copy_dir(src, backup_dir, dest_name):
    """Copy a directory tree to the backup directory."""
    src = Path(src)
    if not src.exists():
        print(f"  SKIP: {src} not found")
        return False
    dest = backup_dir / dest_name
    shutil.copytree(src, dest)
    count = sum(1 for _ in dest.rglob('*') if _.is_file())
    print(f"  Copied: {dest_name}/ ({count} files)")
    return True


def copy_apache_conf(backup_dir):
    """Copy Apache config (needs read access, may need sudo)."""
    dest = backup_dir / 'etaverse.conf'
    try:
        result = subprocess.run(
            ['sudo', '-n', 'cat', str(APACHE_CONF)],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            dest.write_bytes(result.stdout)
            print(f"  Copied: etaverse.conf")
            return True
        else:
            print(f"  SKIP: etaverse.conf (sudo failed: {result.stderr.decode().strip()})")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print(f"  SKIP: etaverse.conf (could not read)")
        return False


def main():
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], '%Y-%m-%d').date()
    else:
        target = datetime.now().date()

    date_str = target.strftime('%Y-%m-%d')
    backup_dir = LOCAL_BACKUP_DIR / date_str

    if backup_dir.exists():
        print(f"Backup already exists: {backup_dir}")
        print("Overwriting.")
        shutil.rmtree(backup_dir)

    backup_dir.mkdir(parents=True)
    print(f"Backing up to {backup_dir}")

    ok = True
    ok = backup_postgres(backup_dir) and ok
    copy_file(TJAI_WWW / '.env', backup_dir, 'env-www.env')
    copy_file(Path.home() / '.env', backup_dir, 'env-home.env')
    copy_dir(TJAI_WWW / 'data', backup_dir, 'data')
    copy_apache_conf(backup_dir)

    if not ok:
        print(f"Backup completed with errors: {date_str}", file=sys.stderr)
        sys.exit(1)

    # Push to Dropbox via rclone
    rclone_dest = f'{RCLONE_DEST}/{date_str}'
    print(f"Pushing to {rclone_dest} ...")
    result = subprocess.run(
        ['rclone', 'copy', str(backup_dir), rclone_dest],
        capture_output=True, text=True, timeout=1800,
    )
    if result.returncode != 0:
        print(f"ERROR: rclone push failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"Backup complete: {date_str} (pushed to Dropbox)")


if __name__ == '__main__':
    main()
