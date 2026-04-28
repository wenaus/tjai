#!/usr/bin/env python3
"""Backup tjai server data, push to Dropbox via rclone.

Creates a dated directory under ~/tjai-backups/server/YYYY-MM-DD/
containing:
  - *-db.sql.gz      PostgreSQL database dumps (compressed)
  - env-www.env      /var/www/tjai/.env
  - env-home.env     ~/.env
  - env-*.env        Production env files for apps with database credentials
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
from urllib.parse import unquote, urlparse

LOCAL_BACKUP_DIR = Path.home() / 'tjai-backups' / 'server'
RCLONE_DEST = 'dropbox:tjai-backups/server'
TJAI_WWW = Path('/var/www/tjai')
APACHE_CONF = Path('/etc/apache2/sites-enabled/etaverse.conf')

DB_DUMPS = [
    {
        'label': 'tjai',
        'env_path': TJAI_WWW / '.env',
        'url_key': 'DJANGO_DATABASE_URL',
        'default_name': 'tjai',
        'default_user': 'tjai',
    },
    {
        'label': 'corun',
        'env_path': Path('/var/www/corun-ai/src/.env'),
        'prefix': 'CORUN_DB_',
        'default_name': 'corun',
        'default_user': 'corun',
    },
    {
        'label': 'swf-remote',
        'env_path': Path('/var/www/swf-remote/src/.env'),
        'prefix': 'SWF_REMOTE_DB_',
        'default_name': 'swf_remote',
        'default_user': 'swf_remote',
    },
    {
        'label': 'etaverse',
        'env_path': Path('/var/www/etaverse-data/.env'),
        'prefix': 'ETAVERSE_DB_',
        'pass_key': 'ETAVERSE_DB_PASS',
        'default_name': 'etaverse',
        'default_user': 'etaverse',
    },
    {
        'label': 'primus',
        'env_path': Path('/var/www/primus/.env'),
        'url_key': 'DJANGO_DATABASE_URL',
        'default_name': 'primus',
        'default_user': 'primus',
    },
    {
        'label': 'pax-eden',
        'env_path': Path('/var/www/pax-eden/.env'),
        'url_key': 'DJANGO_DATABASE_URL',
        'default_name': 'pax_eden',
        'default_user': 'pax_eden',
    },
]

ENV_FILES = [
    (TJAI_WWW / '.env', 'env-www.env'),
    (Path.home() / '.env', 'env-home.env'),
    (Path('/var/www/corun-ai/src/.env'), 'env-corun.env'),
    (Path('/var/www/swf-remote/src/.env'), 'env-swf-remote.env'),
    (Path('/var/www/etaverse-data/.env'), 'env-etaverse.env'),
    (Path('/var/www/primus/.env'), 'env-primus.env'),
    (Path('/var/www/pax-eden/.env'), 'env-pax-eden.env'),
]


def read_env_file(env_path):
    """Read a KEY=VALUE env file without expanding or logging secrets."""
    env_path = Path(env_path)
    env_vars = {}
    if not env_path.exists():
        return env_vars
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[len('export '):].strip()
        key, value = line.split('=', 1)
        env_vars[key.strip()] = value.strip().strip("'\"")
    return env_vars


def parse_db_url(db_url):
    """Return dbname, user, password, host from a postgres URL."""
    parsed = urlparse(db_url)
    dbname = parsed.path.lstrip('/')
    return {
        'dbname': unquote(dbname),
        'dbuser': unquote(parsed.username or ''),
        'dbpass': unquote(parsed.password or ''),
        'dbhost': parsed.hostname or 'localhost',
    }


def db_config_from_env(spec):
    """Build pg_dump connection config from an app's production env file."""
    env_vars = read_env_file(spec['env_path'])
    if not env_vars:
        print(f"  Skipping {spec['label']}: env file not found: {spec['env_path']}", file=sys.stderr)
        return None

    url_key = spec.get('url_key')
    if url_key and env_vars.get(url_key):
        cfg = parse_db_url(env_vars[url_key])
        if cfg['dbname'] and cfg['dbuser'] and cfg['dbpass']:
            return cfg

    prefix = spec.get('prefix', '')
    pass_key = spec.get('pass_key') or f'{prefix}PASSWORD'
    cfg = {
        'dbname': env_vars.get(f'{prefix}NAME', spec['default_name']),
        'dbuser': env_vars.get(f'{prefix}USER', spec['default_user']),
        'dbpass': env_vars.get(pass_key, ''),
        'dbhost': env_vars.get(f'{prefix}HOST', 'localhost'),
    }
    if cfg['dbpass']:
        return cfg

    print(f"  Skipping {spec['label']}: no database password found in {spec['env_path']}", file=sys.stderr)
    return None


def backup_local_db(backup_dir, dbname, dbuser, dbpass, label, dbhost='localhost'):
    """pg_dump a local database, gzipped."""
    dest = backup_dir / f'{label}-db.sql.gz'
    env = os.environ.copy()
    env['PGPASSWORD'] = dbpass

    try:
        dump = subprocess.run(
            ['pg_dump', '-U', dbuser, '-h', dbhost, dbname],
            capture_output=True, env=env, timeout=120,
        )
        if dump.returncode != 0:
            print(f"ERROR: pg_dump {label} failed: {dump.stderr.decode()}", file=sys.stderr)
            return False

        import gzip
        with gzip.open(dest, 'wb') as f:
            f.write(dump.stdout)

        size_mb = dest.stat().st_size / (1024 * 1024)
        print(f"  {label} dump: {dest.name} ({size_mb:.1f} MB)")
        return True
    except subprocess.TimeoutExpired:
        print(f"ERROR: pg_dump {label} timed out after 120s", file=sys.stderr)
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
    for spec in DB_DUMPS:
        cfg = db_config_from_env(spec)
        if not cfg:
            ok = False
            continue
        ok = backup_local_db(
            backup_dir,
            cfg['dbname'],
            cfg['dbuser'],
            cfg['dbpass'],
            spec['label'],
            cfg['dbhost'],
        ) and ok
    for src, dest_name in ENV_FILES:
        copy_file(src, backup_dir, dest_name)
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
        capture_output=True, text=True, timeout=3600,
    )
    if result.returncode != 0:
        print(f"ERROR: rclone push failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"Backup complete: {date_str} (pushed to Dropbox)")


if __name__ == '__main__':
    main()
