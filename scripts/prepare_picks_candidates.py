#!/usr/bin/env python3
"""Build a bounded, deduplicated candidate file for the Picks agent.

The scheduled model should judge candidates, not crawl dozens of sources. This
script collects recent RSS items already in TJAI, discovers feeds on configured
Picks sources, falls back to useful page links, removes URLs already present in
Picks, and writes one compact JSON file for a single curation pass.
"""

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
import sys
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
import requests

try:
    import bootstrap  # noqa: F401 - Django setup
except ModuleNotFoundError:
    from scripts import bootstrap  # noqa: F401 - imported from package in tests

from django.db.models import Q
from tjai_app.models import Entry, RssItem

try:
    from scan_picks_sources import get_sources
except ModuleNotFoundError:
    from scripts.scan_picks_sources import get_sources


AGE_HOURS = 72
FETCH_TIMEOUT = 12
MAX_PER_SOURCE = 10
MAX_CANDIDATES = 180
OUTPUT_PATH = '/var/www/tjai/data/picks-candidates.json'
RSS_CATEGORIES = {'tech', 'science', 'culture', 'google'}
USER_AGENT = 'Mozilla/5.0 (compatible; tjai picks candidate builder)'
TRACKING_QUERY_PREFIXES = ('utm_',)
TRACKING_QUERY_KEYS = {'fbclid', 'gclid', 'mc_cid', 'mc_eid'}


def clean_text(value, limit=600):
    text = re.sub(r'<[^>]+>', ' ', value or '')
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit]


def normalize_url(url):
    """Canonicalize enough for reliable Picks deduplication."""
    try:
        parts = urlsplit((url or '').strip())
    except ValueError:
        return ''
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        return ''
    query = [
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
        and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    path = parts.path.rstrip('/') or '/'
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                       urlencode(query), ''))


def existing_pick_urls():
    urls = set()
    rows = Entry.objects.filter(
        kind='bookmark', context__name='picks', deleted_at__isnull=True,
    ).only('content')
    for entry in rows:
        match = re.search(r'\(\s*(https?://[^\s\)]+)\s*\)', entry.content or '')
        if match:
            normalized = normalize_url(match.group(1))
            if normalized:
                urls.add(normalized)
    return urls


class SourcePageParser(HTMLParser):
    """Extract feed discovery links and readable anchors from a source page."""

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.feed_urls = []
        self.links = []
        self._href = None
        self._text = []
        self._ignored_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'svg'):
            self._ignored_depth += 1
        if tag == 'link':
            rel = attrs.get('rel', '').lower()
            mime = attrs.get('type', '').lower()
            href = attrs.get('href')
            if href and 'alternate' in rel and ('rss' in mime or 'atom' in mime):
                self.feed_urls.append(urljoin(self.base_url, href))
        if tag == 'a' and not self._ignored_depth:
            self._href = attrs.get('href')
            self._text = []

    def handle_endtag(self, tag):
        if tag == 'a' and self._href:
            title = clean_text(''.join(self._text), limit=240)
            self.links.append((urljoin(self.base_url, self._href), title))
            self._href = None
            self._text = []
        if tag in ('script', 'style', 'svg') and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data):
        if self._href and not self._ignored_depth:
            self._text.append(data)


def entry_timestamp(item):
    for attr in ('published_parsed', 'updated_parsed'):
        parsed = getattr(item, attr, None)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pass
    return None


def feed_candidates(content, feed_url, source_name, category, cutoff):
    feed = feedparser.parse(content)
    candidates = []
    for item in feed.entries:
        published = entry_timestamp(item)
        if published and published < cutoff:
            continue
        url = getattr(item, 'link', '') or ''
        title = clean_text(getattr(item, 'title', ''), limit=240)
        if not normalize_url(url) or len(title) < 8:
            continue
        candidates.append({
            'title': title,
            'url': url,
            'source': source_name,
            'category': category,
            'published': published.isoformat() if published else None,
            'summary': clean_text(getattr(item, 'summary', '')),
            'origin': 'source_feed',
        })
        if len(candidates) >= MAX_PER_SOURCE:
            break
    return candidates


_BAD_LINK_TEXT = re.compile(
    r'^(home|about|contact|subscribe|sign in|log in|menu|search|more|next|previous|'
    r'privacy|terms|news|blog|read more|view all)$', re.IGNORECASE,
)


def page_candidates(links, source_url, source_name, category):
    source_host = urlsplit(source_url).netloc.lower().removeprefix('www.')
    candidates = []
    seen = set()
    for url, title in links:
        normalized = normalize_url(url)
        if not normalized or normalized in seen or len(title) < 12:
            continue
        parts = urlsplit(normalized)
        host = parts.netloc.removeprefix('www.')
        if host == source_host and parts.path == '/':
            continue
        if _BAD_LINK_TEXT.match(title):
            continue
        if re.search(r'\.(jpg|jpeg|png|gif|svg|css|js|xml|zip)$', parts.path,
                     re.IGNORECASE):
            continue
        seen.add(normalized)
        candidates.append({
            'title': title,
            'url': url,
            'source': source_name,
            'category': category,
            'published': None,
            'summary': '',
            'origin': 'source_page',
        })
        if len(candidates) >= MAX_PER_SOURCE:
            break
    return candidates


def source_label(raw_line, url):
    annotation = raw_line.lstrip('- ').strip()
    annotation = annotation.removeprefix(url).strip(' —-')
    host = urlsplit(url).netloc.removeprefix('www.')
    return host, annotation


def fetch_source(source, cutoff):
    url, category, raw_line = source
    source_name, annotation = source_label(raw_line, url)
    headers = {'User-Agent': USER_AGENT}
    try:
        if 'arxiv.org/list/' in url:
            subject = url.split('/list/', 1)[1].split('/', 1)[0]
            feed_url = f'https://export.arxiv.org/rss/{subject}'
            response = requests.get(feed_url, timeout=FETCH_TIMEOUT, headers=headers)
            response.raise_for_status()
            candidates = feed_candidates(
                response.content, feed_url, source_name, category, cutoff)
            return candidates, None

        response = requests.get(url, timeout=FETCH_TIMEOUT, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get('content-type', '').lower()
        if 'xml' in content_type or 'rss' in content_type or 'atom' in content_type:
            return feed_candidates(
                response.content, url, source_name, category, cutoff), None

        parser = SourcePageParser(response.url)
        parser.feed(response.text)
        for feed_url in parser.feed_urls[:2]:
            try:
                feed_response = requests.get(
                    feed_url, timeout=FETCH_TIMEOUT, headers=headers)
                feed_response.raise_for_status()
                candidates = feed_candidates(
                    feed_response.content, feed_url, source_name, category, cutoff)
                if candidates:
                    for candidate in candidates:
                        if annotation:
                            candidate['source_note'] = annotation
                    return candidates, None
            except requests.RequestException:
                continue

        candidates = page_candidates(parser.links, response.url, source_name, category)
        for candidate in candidates:
            if annotation:
                candidate['source_note'] = annotation
        return candidates, None
    except requests.RequestException as exc:
        return [], f'{source_name}: {type(exc).__name__}: {str(exc)[:120]}'
    except Exception as exc:
        return [], f'{source_name}: {type(exc).__name__}: {str(exc)[:120]}'


def database_rss_candidates(cutoff):
    rows = RssItem.objects.filter(
        Q(published__gte=cutoff) |
        Q(published__isnull=True, fetched__gte=cutoff),
        category__in=RSS_CATEGORIES,
    ).order_by('-published', '-fetched')
    per_source = defaultdict(int)
    candidates = []
    for item in rows.iterator():
        if per_source[item.source] >= MAX_PER_SOURCE:
            continue
        if not normalize_url(item.url):
            continue
        per_source[item.source] += 1
        candidates.append({
            'title': clean_text(item.title, limit=240),
            'url': item.url,
            'source': item.source,
            'category': item.category,
            'published': item.published.isoformat() if item.published else None,
            'summary': clean_text(item.precis),
            'origin': 'rss_reader',
        })
    return candidates


def build_candidates():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=AGE_HOURS)
    candidates = database_rss_candidates(cutoff)
    errors = []
    sources = get_sources()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_source, source, cutoff): source for source in sources}
        for future in as_completed(futures):
            found, error = future.result()
            candidates.extend(found)
            if error:
                errors.append(error)

    already_picked = existing_pick_urls()
    seen = set(already_picked)
    by_source = defaultdict(list)
    for candidate in candidates:
        normalized = normalize_url(candidate.get('url'))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        candidate['url'] = normalized
        by_source[candidate.get('source') or 'unknown'].append(candidate)

    # Round-robin across sources so a high-volume feed cannot crowd out the
    # small specialist sources that make Picks personally useful.
    unique = []
    source_groups = list(by_source.values())
    rank = 0
    while len(unique) < MAX_CANDIDATES:
        added = False
        for group in source_groups:
            if rank < len(group):
                unique.append(group[rank])
                added = True
                if len(unique) >= MAX_CANDIDATES:
                    break
        if not added:
            break
        rank += 1

    return {
        'generated_at': now.isoformat(),
        'age_hours': AGE_HOURS,
        'configured_sources': len(sources),
        'candidate_count': len(unique),
        'source_errors': sorted(errors),
        'candidates': unique,
    }


def reset_progress_log(payload):
    now = datetime.now(timezone.utc)
    content = (
        f'Picks run started {now.strftime("%Y-%m-%dT%H:%M:%SZ")}\n'
        f'Candidate preparation: {payload["candidate_count"]} candidates from '
        f'{payload["configured_sources"]} configured sources; '
        f'{len(payload["source_errors"])} source errors; existing Picks removed'
    )
    log = Entry.objects.filter(
        data__entry_id='picks-run-log', deleted_at__isnull=True,
    ).order_by('-timestamp_created').first()
    if log:
        log.content = content
        log.timestamp_modified = now.timestamp()
        log.save(update_fields=['content', 'timestamp_modified'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    payload = build_candidates()
    output = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.dry_run:
        print(output)
        return

    output_path = os.environ.get('TJAI_PICKS_CANDIDATES_PATH', OUTPUT_PATH)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as handle:
        handle.write(output + '\n')
    reset_progress_log(payload)
    print(
        f'Wrote {payload["candidate_count"]} candidates to {output_path}; '
        f'{len(payload["source_errors"])} source errors'
    )
    for error in payload['source_errors']:
        print(f'  SOURCE ERROR: {error}', file=sys.stderr)


if __name__ == '__main__':
    main()
