#!/usr/bin/env python3
"""Refresh web pages so their server-side cached products stay current.

Reads page URLs from the page-refresh-pages tjai entry (one URL per
line; other lines are ignored). Each URL is fetched with refresh=1
appended, the cached-product platform's synchronous-rebuild path, so
the fetch is the build and a 200 means the stored product is current.
Fetches run sequentially with a fixed gap between them to serialize
load on the serving host.

URLs under epic-devcloud.org/prod sit behind the swf-remote login wall
and are fetched as the claude-ec2dev service account (credential file
documented in the ec2dev machine guidance). Other URLs are fetched
anonymously.

Every page is attempted; any failure makes the script exit nonzero
listing all failures, which fails the wrangler worker and surfaces to
AppLog and Capcom.

Usage:
    python page_refresh.py            # refresh all listed pages
    python page_refresh.py --url URL  # refresh one URL only
"""
import argparse
import re
import sys
import time

import requests

import bootstrap  # noqa: F401 - Django setup

from tjai_app.models import Entry

PAGES_ENTRY_ID = 'page-refresh-pages'
CREDENTIAL_FILE = '/home/admin/.swf-remote-claude-ec2dev.env'
LOGIN_URL = 'https://epic-devcloud.org/prod/accounts/login/'
WALLED_PREFIX = 'https://epic-devcloud.org/prod'
FETCH_TIMEOUT = 120
GAP_SECONDS = 10


def get_pages():
    """Return the URL list from the page-refresh-pages entry."""
    entry = Entry.objects.filter(
        data__entry_id=PAGES_ENTRY_ID,
        deleted_at__isnull=True,
    ).first()
    if not entry:
        print(f"ERROR: {PAGES_ENTRY_ID} entry not found", file=sys.stderr)
        sys.exit(1)
    return [line.strip().split()[0]
            for line in entry.content.splitlines()
            if line.strip().startswith(('http://', 'https://'))]


def read_credential():
    """Return (user, password) from the service-account credential file."""
    creds = {}
    with open(CREDENTIAL_FILE) as f:
        for line in f:
            if '=' in line:
                key, _, value = line.strip().partition('=')
                creds[key] = value
    return (creds['SWF_REMOTE_CLAUDE_USER'],
            creds['SWF_REMOTE_CLAUDE_PASSWORD'])


def login(session):
    """Sign the session in to epic-devcloud.org. Raises on failure."""
    user, password = read_credential()
    page = session.get(LOGIN_URL, timeout=FETCH_TIMEOUT)
    page.raise_for_status()
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text)
    if not match:
        raise RuntimeError("no CSRF token on login page")
    response = session.post(
        LOGIN_URL,
        data={'csrfmiddlewaretoken': match.group(1),
              'username': user, 'password': password},
        headers={'Referer': LOGIN_URL},
        timeout=FETCH_TIMEOUT,
    )
    response.raise_for_status()
    if '/accounts/login/' in response.url:
        raise RuntimeError("login rejected (still on the login page)")


def refresh_url(url):
    """URL with refresh=1 appended."""
    return url + ('&' if '?' in url else '?') + 'refresh=1'


def fetch(session, url):
    """Fetch one page's synchronous rebuild. Returns None or an error string."""
    started = time.time()
    try:
        response = session.get(refresh_url(url), timeout=FETCH_TIMEOUT)
    except requests.RequestException as e:
        return f"request failed: {e}"
    if '/accounts/login/' in response.url:
        return "bounced to login (session not accepted)"
    if response.status_code != 200:
        return f"HTTP {response.status_code}"
    built = re.search(r'Built at [^<]*', response.text)
    print(f"ok {url} ({time.time() - started:.1f}s"
          f"{', ' + built.group(0) if built else ''})")
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', help="refresh this single URL only")
    args = parser.parse_args()

    pages = [args.url] if args.url else get_pages()
    if not pages:
        print("no pages listed — nothing to do")
        return

    session = requests.Session()
    login_error = None
    if any(url.startswith(WALLED_PREFIX) for url in pages):
        try:
            login(session)
        except Exception as e:
            login_error = str(e)

    failures = []
    first = True
    for url in pages:
        if not first:
            time.sleep(GAP_SECONDS)
        first = False
        if login_error and url.startswith(WALLED_PREFIX):
            failures.append((url, f"login failed: {login_error}"))
            continue
        error = fetch(session, url)
        if error:
            failures.append((url, error))

    if failures:
        for url, error in failures:
            print(f"FAILED {url}: {error}", file=sys.stderr)
        sys.exit(1)
    print(f"refreshed {len(pages)} page(s)")


if __name__ == '__main__':
    main()
