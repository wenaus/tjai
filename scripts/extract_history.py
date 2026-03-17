"""Extract '## Today in History' from daily synopsis entries and save as HTML.

Reads daily-{YYYY-MM-DD} entries, extracts the '## Today in History' section,
and writes self-contained HTML to /var/www/public/today-in-history/{MM-DD}.html.
Dark/light mode via ?dark=1 query param (client-side JS).

Usage:
    python extract_history.py              # today
    python extract_history.py 2026-03-16   # specific date
    python extract_history.py --backfill   # all existing dailies
"""
import argparse
import html
import os
import re
import sys
from datetime import datetime

import bootstrap  # noqa: F401 - Django setup

from tjai_app.models import Entry

HISTORY_DIR = '/var/www/public/today-in-history'
HEADING = '## Today in History'


def extract_section(content, heading):
    """Extract a ## section from markdown content. Returns body or None."""
    pattern = re.compile(r'^' + re.escape(heading) + r'\s*$', re.MULTILINE)
    match = pattern.search(content or '')
    if not match:
        return None
    rest = content[match.end():]
    next_heading = re.search(r'\n^## ', rest, re.MULTILINE)
    if next_heading:
        body = rest[:next_heading.start()]
    else:
        body = rest
    return body.strip()


def md_to_html(body):
    """Convert history markdown body to HTML fragments."""
    escaped = html.escape(body)
    out = escaped
    out = re.sub(r'^### (.+)$', r'<h3>\1</h3>', out, flags=re.MULTILINE)
    out = re.sub(r'^\d+\. (.+)$', r'<li>\1</li>', out, flags=re.MULTILINE)
    out = re.sub(r'((?:<li>.*</li>\n?)+)', r'<ol>\1</ol>', out)
    out = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', out)
    out = re.sub(r'\n{2,}', '\n', out)
    return out


def past_days_links(exclude_mm_dd):
    """Build HTML list linking to other available history files.

    Returns reverse-chronological list (going backwards from the day before
    exclude_mm_dd, wrapping around the year).
    """
    try:
        files = os.listdir(HISTORY_DIR)
    except OSError:
        return ''
    dates = set()
    for f in files:
        m = re.match(r'^(\d{2}-\d{2})\.html$', f)
        if m and m.group(1) != exclude_mm_dd:
            dates.add(m.group(1))
    if not dates:
        return ''

    def ordinal(mm_dd):
        return (datetime.strptime(f'2024-{mm_dd}', '%Y-%m-%d').date()
                - datetime.strptime('2024-01-01', '%Y-%m-%d').date()).days

    ref = ordinal(exclude_mm_dd)
    sorted_dates = sorted(dates, key=lambda d: (ref - ordinal(d)) % 366 or 366)

    items = []
    for d in sorted_dates:
        label = datetime.strptime(f'2024-{d}', '%Y-%m-%d').strftime('%B %-d')
        items.append(f'<li><a href="/public/today-in-history/{d}.html">{label}</a></li>')
    return '<hr><h3>Past days in history</h3>\n<ul>\n' + '\n'.join(items) + '\n</ul>'


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Today in History — {title_date}</title>
<style>
body {{ font-family:sans-serif; padding:24px 32px; max-width:700px; margin:0 auto; font-size:1.1em; line-height:1.6; }}
body.light {{ background:#f5f5f5; color:#222; }}
body.dark {{ background:#1a1a1a; color:#e0e0e0; }}
h2 {{ margin:20px 0 12px; }}
.light h2 {{ color:#c05a20; }} .dark h2 {{ color:#d47830; }}
h3 {{ margin:16px 0 8px; }}
.light h3 {{ color:#2a6e3f; }} .dark h3 {{ color:#6cbf84; }}
ol {{ margin:4px 0 12px 24px; }}
li {{ margin:4px 0; }}
.light b {{ color:#000; }} .dark b {{ color:#fff; }}
a {{ text-decoration:none; }}
.light a {{ color:#1a5ea0; }} .dark a {{ color:#6ca6d4; }}
a:hover {{ text-decoration:underline; }}
ul {{ list-style:none; padding:0; }}
hr {{ border:none; margin:32px 0 16px; }}
.light hr {{ border-top:1px solid #ccc; }} .dark hr {{ border-top:1px solid #444; }}
</style>
<script>
document.addEventListener('DOMContentLoaded', function() {{
  var dark = new URLSearchParams(location.search).get('dark') === '1';
  document.body.className = dark ? 'dark' : 'light';
  // Propagate dark param to all internal links
  if (dark) {{
    document.querySelectorAll('a[href]').forEach(function(a) {{
      var h = a.getAttribute('href');
      if (h.startsWith('/')) a.setAttribute('href', h + '?dark=1');
    }});
  }}
}});
</script>
</head>
<body class="light">
<h2>Today in History — {title_date}</h2>
{body_html}
{past_html}
</body></html>
"""


def save_history(date_str, body):
    """Save extracted history as self-contained HTML."""
    d = datetime.strptime(date_str, '%Y-%m-%d').date()
    mm_dd = d.strftime('%m-%d')
    title_date = d.strftime('%B %-d')
    filepath = os.path.join(HISTORY_DIR, f'{mm_dd}.html')
    os.makedirs(HISTORY_DIR, exist_ok=True)

    body_html = md_to_html(body)
    past_html = past_days_links(mm_dd)

    with open(filepath, 'w') as f:
        f.write(HTML_TEMPLATE.format(
            title_date=title_date,
            body_html=body_html,
            past_html=past_html,
        ))
    return filepath


def process_date(date_str):
    """Extract and save history for one date. Returns True if saved."""
    entry_id = f'daily-{date_str}'
    entry = Entry.objects.filter(
        data__entry_id=entry_id,
        deleted_at__isnull=True,
    ).first()
    if not entry:
        return False
    body = extract_section(entry.content, HEADING)
    if not body:
        return False
    path = save_history(date_str, body)
    print(f'  {entry_id} -> {os.path.basename(path)}')
    return True


def backfill():
    """Process all existing daily entries."""
    entries = Entry.objects.filter(
        data__entry_id__startswith='daily-',
        kind='journal',
        deleted_at__isnull=True,
    ).order_by('data__entry_id')

    count = 0
    for entry in entries:
        eid = entry.data.get('entry_id', '')
        date_str = eid.replace('daily-', '')
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            continue
        body = extract_section(entry.content, HEADING)
        if body:
            save_history(date_str, body)
            print(f'  {eid} -> {date_str[5:]}.html')
            count += 1
    print(f'Backfilled {count} history files.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extract Today in History from daily synopsis')
    parser.add_argument('date', nargs='?', default=None, help='Date YYYY-MM-DD (default: today)')
    parser.add_argument('--backfill', action='store_true', help='Process all existing dailies')
    args = parser.parse_args()

    if args.backfill:
        backfill()
    else:
        if args.date:
            date_str = args.date
        else:
            from tjai_app.services import get_timezone
            date_str = datetime.now(get_timezone()).date().isoformat()

        if process_date(date_str):
            print('Done.')
        else:
            print(f'No history section found for {date_str}')
            sys.exit(1)
