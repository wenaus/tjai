"""Parser for Dynalist export files.

Dynalist exports to .txt and .opml formats. This parser handles .txt exports
which have markdown links and hashtags.

Format:
    - Section headers: "March 2025", "Nov 2024", etc.
    - Bookmarks: #tag #tag2 [Title](URL)
    - Indentation indicates hierarchy (tabs)
"""

import re
import calendar
from datetime import datetime
from pathlib import Path


def parse_month_header(line):
    """Parse month headers like 'March 2025', 'Nov 2024', 'Apr 2023'.

    Returns YYYYMMDD string for first of month, or None if not a header.
    """
    months_full = {m: i for i, m in enumerate(calendar.month_name) if m}
    months_abbr = {m: i for i, m in enumerate(calendar.month_abbr) if m}
    months = {**months_full, **months_abbr}

    pattern = r'^(' + '|'.join(months.keys()) + r')\s+(\d{4})$'
    match = re.match(pattern, line.strip())
    if match:
        month_name, year = match.groups()
        month_num = months[month_name]
        return f"{year}{month_num:02d}01"
    return None


def date_str_to_timestamp(date_str):
    """Convert YYYYMMDD to Unix timestamp (noon UTC)."""
    dt = datetime.strptime(date_str, '%Y%m%d').replace(hour=12)
    return dt.timestamp()


def extract_bookmarks_from_file(filepath, default_date_str):
    """Extract bookmarks from a Dynalist .txt export.

    Args:
        filepath: Path to the .txt file
        default_date_str: YYYYMMDD to use when no month header found

    Returns:
        List of dicts in standard import format
    """
    bookmarks = []
    current_date = default_date_str

    # Domains to skip (internal/personal links)
    skip_domains = [
        'chrome.google.com/webstore',
        'dynalist.io',
        'mail.google.com',
        'dropbox.com',
        'docs.google.com',
        'drive.google.com',
    ]

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            # Check for month header
            month_date = parse_month_header(line)
            if month_date:
                current_date = month_date
                continue

            # Find markdown links [Title](URL)
            link_pattern = r'\[([^\]]+)\]\((https?://[^)]+)\)'
            matches = re.findall(link_pattern, line)

            if not matches:
                continue

            # Extract hashtags from the line
            tag_pattern = r'#([a-zA-Z][a-zA-Z0-9_-]*)'
            tags = re.findall(tag_pattern, line)

            for title, url in matches:
                # Skip filtered domains
                if any(skip in url for skip in skip_domains):
                    continue

                bookmarks.append({
                    'content': f'[{title.strip()}]({url.strip()})',
                    'tags': tags,
                    'timestamp': date_str_to_timestamp(current_date),
                    'context': None,
                })

    return bookmarks


def deduplicate_by_url(bookmarks):
    """Deduplicate bookmarks by URL (stripped of query params).

    Keeps first occurrence.
    """
    seen = set()
    result = []
    for bm in bookmarks:
        # Extract URL from markdown content
        match = re.search(r'\]\((https?://[^)]+)\)', bm['content'])
        if match:
            base_url = match.group(1).split('?')[0]
            if base_url not in seen:
                seen.add(base_url)
                result.append(bm)
        else:
            result.append(bm)
    return result


def parse_dynalist(links_file=None, todo_file=None, default_date='20250707',
                   deduplicate=True):
    """Parse Dynalist export files and return bookmarks in standard format.

    Args:
        links_file: Path to Links.txt export (optional)
        todo_file: Path to ToDo.txt export (optional)
        default_date: YYYYMMDD for entries without month headers
        deduplicate: Whether to deduplicate by URL (default True)

    Returns:
        List of dicts with: content, tags, timestamp, context
    """
    all_bookmarks = []

    if links_file:
        path = Path(links_file)
        if path.exists():
            # Links.txt has month headers, use March 2025 as default
            bm = extract_bookmarks_from_file(path, '20250301')
            all_bookmarks.extend(bm)

    if todo_file:
        path = Path(todo_file)
        if path.exists():
            # ToDo.txt typically doesn't have month headers
            bm = extract_bookmarks_from_file(path, default_date)
            all_bookmarks.extend(bm)

    if deduplicate:
        all_bookmarks = deduplicate_by_url(all_bookmarks)

    return all_bookmarks


def parse_dynalist_zip(zip_path, default_date='20250707', deduplicate=True):
    """Parse a Dynalist backup zip file.

    Extracts Links.txt and ToDo.txt and parses them.

    Args:
        zip_path: Path to the .zip backup file
        default_date: YYYYMMDD for entries without month headers
        deduplicate: Whether to deduplicate by URL

    Returns:
        List of dicts in standard import format
    """
    import tempfile
    import zipfile

    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Extract only the files we need
            links_path = None
            todo_path = None

            for name in zf.namelist():
                if name == 'Links.txt':
                    zf.extract(name, tmpdir)
                    links_path = Path(tmpdir) / name
                elif name == 'ToDo.txt':
                    zf.extract(name, tmpdir)
                    todo_path = Path(tmpdir) / name

            return parse_dynalist(
                links_file=links_path,
                todo_file=todo_path,
                default_date=default_date,
                deduplicate=deduplicate
            )
