#!/usr/bin/env python3
"""Fetch Wikipedia 'on this day' and write complete event list to data/history/.

The mechanical part: fetch, format, save. AI selection happens separately.

Usage:
    python daily_history.py              # tomorrow
    python daily_history.py today        # today
    python daily_history.py 2026-02-22   # specific date
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

WIKI_API = 'https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/{month:02d}/{day:02d}'
USER_AGENT = 'tjai-daily/1.0 (torre@wenaus.org)'
HISTORY_DIR = Path(__file__).resolve().parent.parent / 'data' / 'history'


def fetch_on_this_day(month, day, attempts=3, wait=60):
    """Fetch Wikipedia on-this-day data, retrying a transient failure.

    A gateway timeout (2026-09-11) cost the whole night's product; the
    schedule's next firing is a day away.
    """
    url = WIKI_API.format(month=month, day=day)
    req = Request(url, headers={'User-Agent': USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except URLError as e:
            print(f"Error fetching Wikipedia API (attempt {attempt}/{attempts}): {e}",
                  file=sys.stderr)
            if attempt < attempts:
                time.sleep(wait)
    return None


def format_item(item):
    """Format a single event/birth/death as a markdown line."""
    year = item.get('year', '?')
    text = item.get('text', '').strip()
    return f"- {text} ({year})"


def build_complete(data):
    """Build complete markdown with all events, births, deaths."""
    lines = []

    # Merge selected + events, deduplicate
    selected = data.get('selected', [])
    selected_texts = {s.get('text', '') for s in selected}
    events = [e for e in data.get('events', []) if e.get('text', '') not in selected_texts]

    if selected or events:
        lines.append('### Events')
        for item in selected:
            lines.append(format_item(item))
        if events:
            for item in events:
                lines.append(format_item(item))

    births = data.get('births', [])
    if births:
        lines.append('')
        lines.append('### Birthdays')
        for item in births:
            lines.append(format_item(item))


    return '\n'.join(lines)


def write_complete(target_date, data):
    """Write complete markdown file to data/history/."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    date_str = target_date.strftime('%a %b %-d, %Y')
    prefix = target_date.strftime('%m-%d')

    content = f"# On This Day: {date_str}\n\n{build_complete(data)}\n"
    path = HISTORY_DIR / f"{prefix}-complete.md"
    path.write_text(content)
    print(f"Wrote {path}")

    sel = len(data.get('selected', []))
    ev = len(data.get('events', []))
    bi = len(data.get('births', []))
    print(f"Stats: {sel} selected, {ev} events, {bi} births")


def main():
    parser = argparse.ArgumentParser(description='Fetch Wikipedia on-this-day data')
    parser.add_argument('date', nargs='?', default=None,
                        help='Target date: "today", YYYY-MM-DD, or omit for tomorrow')
    args = parser.parse_args()

    if args.date == 'today':
        target = datetime.now().date()
    elif args.date:
        target = datetime.strptime(args.date, '%Y-%m-%d').date()
    else:
        target = (datetime.now() + timedelta(days=1)).date()

    print(f"Fetching on-this-day for {target.strftime('%B %-d')}...")
    data = fetch_on_this_day(target.month, target.day)
    if not data:
        sys.exit(1)

    write_complete(target, data)


if __name__ == '__main__':
    main()
