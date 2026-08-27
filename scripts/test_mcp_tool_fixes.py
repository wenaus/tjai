#!/usr/bin/env python3
"""Functional checks for the MCP tool fixes of 2026-08-27:

- get_calendar: entry ids in results, title truncation, days=N returns N days
- search_entries: journal date filters apply to the event date
- full-text search: slashes tokenize as word separators ("testbed" matches
  "Prod/testbed meeting"; trigger + backfill in migration 0025)

Read-only against the configured database; run after deploy/migrate.
"""

import os
import sys
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tjai_project.settings.base")
os.environ.setdefault("DJANGO_DATABASE_URL", "postgresql://tjai:tjai@localhost:5432/tjai")

import django  # noqa: E402

django.setup()

from tjai_app import services  # noqa: E402
from tjai_app.models import Entry  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("ok:  " if cond else "FAIL: ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# fts_normalize
check("fts_normalize replaces slashes",
      services.fts_normalize("Prod/testbed meeting") == "Prod testbed meeting")

# get_calendar: days=1 is exactly one day; ids present; titles truncated
cal = services.get_calendar(start_date="20260821", days=1)
check("get_calendar returns a list", isinstance(cal, list), str(cal)[:200])
if isinstance(cal, list):
    check("days=1 spans a single day", {r["date"] for r in cal} <= {"2026-08-21"},
          str({r["date"] for r in cal}))
    check("calendar entries carry ids", all(r.get("id") for r in cal))
    check("calendar titles truncated to default 500",
          all(len(r["title"]) <= 501 for r in cal))

check("days=0 rejected", isinstance(services.get_calendar(days=0), dict))

cal2 = services.get_calendar(start_date="20260821", days=2)
if isinstance(cal2, list):
    check("days=2 spans two days", {r["date"] for r in cal2} <= {"2026-08-21", "2026-08-22"},
          str({r["date"] for r in cal2}))

# search_entries: journal date filters apply to the event date
tz = services.get_timezone()
day_start = datetime(2026, 7, 24, tzinfo=tz).timestamp()
res = services.search_entries(kind="journal", start_date="20260724", end_date="20260724", limit=100)
check("journal event-date listing returns results",
      isinstance(res, list) and len(res) > 0, str(res)[:200])
if isinstance(res, list) and res:
    check("all results have event_date on the requested day",
          all(day_start <= e.get("data", {}).get("event_date", -1) < day_start + 86400 for e in res))

res_mod = services.search_entries(kind="journal", start_date="20260724", end_date="20260724",
                                  limit=100, date_field="modified")
if isinstance(res_mod, list) and res_mod:
    check("date_field='modified' filters on modification date",
          all(datetime.fromisoformat(e["modified"]).date() == date(2026, 7, 24) for e in res_mod))

# full-text search: slashed compound found by its component words
target = Entry.objects.filter(kind="journal", deleted_at__isnull=True,
                              content__istartswith="Prod/testbed meeting").first()
if target:
    phrase = services.search_entries(query='"prod testbed meeting"', kind="journal", limit=200)
    check("phrase query matches slashed title",
          isinstance(phrase, list) and target.id in {e["id"] for e in phrase})
    # Word probe scoped to the target's own event day — several weekly copies
    # of this meeting exist and .first() picks an arbitrary one.
    target_day = datetime.fromtimestamp(target.data["event_date"], tz=tz).strftime("%Y%m%d")
    word = services.search_entries(query="testbed", kind="journal",
                                   start_date=target_day, end_date=target_day)
    check("single word matches slashed title",
          isinstance(word, list) and target.id in {e["id"] for e in word})
else:
    print("skip: no 'Prod/testbed meeting' journal entry to probe")

print(f"\n{len(failures)} failure(s)" if failures else "\nall checks passed")
sys.exit(1 if failures else 0)
