#!/usr/bin/env python3
"""Fetch RSS feeds and store new items in the rss_items table.

Reads feed URLs from the rss-sources tjai entry (grouped by category
under ## headings). Fetches each feed, deduplicates by guid/url, and
inserts new items.

Usage:
    python fetch_rss.py              # fetch all feeds
    python fetch_rss.py --source URL # fetch one feed only
"""
import argparse
import hashlib
import sys
import traceback
from datetime import datetime, timedelta, timezone

import feedparser

import bootstrap  # noqa: F401 - Django setup

MAX_AGE_DAYS = 14
from tjai_app.models import Entry, RssItem


def get_sources():
    """Parse rss-sources entry into {category: [url, ...]}."""
    entry = Entry.objects.filter(
        data__entry_id='rss-sources',
        deleted_at__isnull=True,
    ).first()
    if not entry:
        print("ERROR: rss-sources entry not found", file=sys.stderr)
        sys.exit(1)

    sources = {}
    current_category = 'uncategorized'
    for line in entry.content.splitlines():
        line = line.strip()
        if line.startswith('## '):
            current_category = line[3:].strip().lower()
            continue
        if line.startswith('http://') or line.startswith('https://'):
            url = line.split()[0]  # take URL, ignore trailing comments
            sources.setdefault(current_category, []).append(url)
    return sources


def make_guid(entry, feed_url):
    """Get a stable unique ID for a feed entry."""
    # Prefer the feed's own guid/id
    guid = getattr(entry, 'id', None) or ''
    if guid:
        return hashlib.sha256(guid.encode()).hexdigest()[:64]
    # Fall back to URL
    link = getattr(entry, 'link', None) or ''
    if link:
        return hashlib.sha256(link.encode()).hexdigest()[:64]
    # Last resort: hash title + feed_url
    title = getattr(entry, 'title', '') or ''
    raw = f"{feed_url}:{title}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def parse_published(entry):
    """Extract published datetime from a feed entry."""
    for attr in ('published_parsed', 'updated_parsed'):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    return None


def fetch_feed(feed_url, category):
    """Fetch one feed and insert new items. Returns (new_count, error_msg)."""
    try:
        feed = feedparser.parse(feed_url)
    except Exception as e:
        return 0, f"feedparser error: {e}"

    if feed.bozo and not feed.entries:
        bozo_msg = str(getattr(feed, 'bozo_exception', 'unknown error'))
        return 0, f"feed error: {bozo_msg}"

    source_name = feed.feed.get('title', feed_url) if hasattr(feed, 'feed') else feed_url
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    new_count = 0

    for fe in feed.entries:
        guid = make_guid(fe, feed_url)

        # Skip if already in DB
        if RssItem.objects.filter(guid=guid).exists():
            continue

        title = getattr(fe, 'title', '(no title)') or '(no title)'
        link = getattr(fe, 'link', '') or ''
        summary = getattr(fe, 'summary', '') or ''
        # Truncate long summaries
        if len(summary) > 500:
            summary = summary[:497] + '...'
        published = parse_published(fe)

        # Skip items older than cutoff
        if published and published < cutoff:
            continue

        RssItem.objects.create(
            guid=guid,
            feed_url=feed_url,
            source=source_name,
            category=category,
            title=title,
            url=link,
            precis=summary,
            published=published,
            fetched=now,
            read=False,
        )
        new_count += 1

    return new_count, None


def main():
    parser = argparse.ArgumentParser(description='Fetch RSS feeds')
    parser.add_argument('--source', help='Fetch a single feed URL only')
    args = parser.parse_args()

    sources = get_sources()

    if args.source:
        # Find category for this URL
        cat = 'uncategorized'
        for category, urls in sources.items():
            if args.source in urls:
                cat = category
                break
        new, err = fetch_feed(args.source, cat)
        if err:
            print(f"ERROR {args.source}: {err}", file=sys.stderr)
            sys.exit(1)
        print(f"{args.source}: {new} new items")
        return

    total_new = 0
    errors = []
    for category, urls in sources.items():
        for url in urls:
            try:
                new, err = fetch_feed(url, category)
                if err:
                    errors.append(f"{url}: {err}")
                    print(f"  ERROR {url}: {err}", file=sys.stderr)
                else:
                    if new > 0:
                        print(f"  {url}: {new} new")
                    total_new += new
            except Exception:
                tb = traceback.format_exc()
                errors.append(f"{url}: {tb}")
                print(f"  EXCEPTION {url}:\n{tb}", file=sys.stderr)

    print(f"Done: {total_new} new items, {len(errors)} errors")
    if errors:
        sys.exit(1)


if __name__ == '__main__':
    main()
