#!/usr/bin/env python3
"""Pre-scan picks sources with hard timeouts before AI dispatch.

Fetches each URL from picks-sources with a 10-second timeout. Writes a
clean source list to /var/www/tjai/data/picks-sources-live.md containing
only the sources that responded. Failed sources are logged but excluded.

Usage:
    python scan_picks_sources.py           # scan all, write clean list
    python scan_picks_sources.py --dry-run # scan all, print only
"""
import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import bootstrap  # noqa: F401 - Django setup

from tjai_app.models import Entry

TIMEOUT = 10  # seconds per source
OUTPUT_PATH = '/var/www/tjai/data/picks-sources-live.md'


def get_sources():
    """Parse picks-sources entry into [(url, annotation, category, raw_line), ...]."""
    entry = Entry.objects.filter(
        data__entry_id='picks-sources',
        deleted_at__isnull=True,
    ).first()
    if not entry:
        print("ERROR: picks-sources entry not found", file=sys.stderr)
        sys.exit(1)

    sources = []
    category = ''
    for line in entry.content.splitlines():
        stripped = line.strip()
        if stripped.startswith('## '):
            category = stripped[3:].strip()
            continue
        # Parse "- URL — annotation"
        m = re.match(r'^-\s+(https?://\S+)\s*(.*)?$', stripped)
        if m:
            sources.append((m.group(1), category, stripped))
            continue
        # Reddit subreddits
        m = re.match(r'^-\s+(r/\S+)\s*(.*)?$', stripped)
        if m:
            url = f'https://www.reddit.com/{m.group(1)}/hot/.json?limit=15'
            sources.append((url, category, stripped))
    return sources


def check_one(url, category, raw_line):
    """Check if a URL responds within timeout. Returns (url, category, raw_line, ok, status, elapsed)."""
    start = time.time()
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; tjai picks scanner)'}
        resp = requests.head(url, timeout=TIMEOUT, headers=headers,
                             allow_redirects=True)
        elapsed = time.time() - start
        # HEAD may 405, fall back to GET
        if resp.status_code == 405:
            resp = requests.get(url, timeout=TIMEOUT, headers=headers,
                                allow_redirects=True, stream=True)
            resp.close()
            elapsed = time.time() - start
        ok = resp.status_code < 400
        return (url, category, raw_line, ok, resp.status_code, elapsed)
    except requests.Timeout:
        return (url, category, raw_line, False, 'timeout', time.time() - start)
    except requests.ConnectionError:
        return (url, category, raw_line, False, 'conn_error', time.time() - start)
    except Exception as e:
        return (url, category, raw_line, False, str(e)[:50], time.time() - start)


def scan_all(sources):
    """Scan all sources in parallel. Returns list of result tuples."""
    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(check_one, url, cat, raw): url
                   for url, cat, raw in sources}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda r: (r[1], r[0]))
    return results


def write_clean_list(results, dry_run=False):
    """Write clean source list with only reachable sources."""
    ok = [r for r in results if r[3]]
    failed = [r for r in results if not r[3]]

    # Build clean file: same format as picks-sources, only live sources
    lines = []
    current_cat = ''
    for url, cat, raw_line, _, _, _ in ok:
        if cat != current_cat:
            if current_cat:
                lines.append('')
            lines.append(f'## {cat}')
            current_cat = cat
        lines.append(f'- {raw_line.lstrip("- ")}')

    output = '\n'.join(lines) + '\n'

    if dry_run:
        print(output)
    else:
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, 'w') as f:
            f.write(output)

    # Log results
    print(f'{len(ok)} sources live, {len(failed)} excluded')
    for url, cat, _, _, status, elapsed in failed:
        print(f'  EXCLUDED: {url} ({status}, {elapsed:.1f}s)')

    return len(ok), len(failed)


def reset_progress_log(ok_count, failed_count):
    """Reset the picks-run-log entry to a fresh run header."""
    from datetime import datetime as dt, timezone
    now = dt.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    content = f'Picks run started {now}\nPre-scan: {ok_count} sources live, {failed_count} excluded'
    log = Entry.objects.filter(
        data__entry_id='picks-run-log',
        deleted_at__isnull=True,
    ).order_by('-timestamp_created').first()
    if log:
        log.content = content
        log.timestamp_modified = dt.now(timezone.utc).timestamp()
        log.save(update_fields=['content', 'timestamp_modified'])
        print(f'Progress log reset: {log.id}')
    else:
        print('WARNING: picks-run-log entry not found, AI will need to create it')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Pre-scan picks sources')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    sources = get_sources()
    print(f'Scanning {len(sources)} sources...')
    results = scan_all(sources)
    ok, failed = write_clean_list(results, dry_run=args.dry_run)
    if not args.dry_run:
        reset_progress_log(ok, failed)
