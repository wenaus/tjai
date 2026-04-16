#!/usr/bin/env python3
"""Build the weekly workweek_<yyyymmdd> entry from the prior Sat-Fri workdays.

Invoked Sat 05:00 EDT as the workweek-agent action's mechanical_script.
Steps:
  1. Compute the prior Sat-Fri (relative to today, in user TZ).
  2. Load each workday_<yyyymmdd> entry for that range.
  3. Concatenate the dailies into a single workweek_input_<lastSat> entry
     (provenance: shows exactly what the AI was given).
  4. Call Claude with the concatenation + an instruction to produce a
     topically organized weekly summary.
  5. Save the result as workweek_<lastSat> entry.
"""
import os
import sys
import time
from datetime import datetime, timedelta

import bootstrap  # noqa: F401 - Django setup

from tjai_app import services
from tjai_app.models import Entry
from tjai_app.services import get_timezone

import logging
logger = logging.getLogger('workweek_agent')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)
    try:
        from tjai_app.db_log_handler import DbLogHandler
        _db = DbLogHandler(source='workweek_agent')
        _db.setFormatter(_fmt)
        logger.addHandler(_db)
    except Exception:
        pass


SUMMARY_INSTRUCTION = """You are summarizing a week of activity for the user.

Below are seven daily activity reports (Saturday through Friday). Produce a
TOPICAL summary of the week organized by project / area / theme — NOT day by
day. Each topic gets a `### <Topic>` section with bulleted points covering what
was done across the week in that area.

Cover everything substantive — code shipped, debugging, exploration,
discussions, decisions. Do not editorialize, hype, or invent. If a day is
sparse or empty, that is fine — do not pad. Be concrete: name the project,
the change, the outcome.

Output is markdown only. No preamble, no closing remarks. Start with the
first topic header on the first line.
"""


def _compute_prior_week(today):
    """Return (last_saturday_date, [date for each of Sat..Fri]).

    last_saturday is the Saturday of the most recently completed Sat-Fri week.
    On a Saturday run, that is today minus 7 days (the prior Sat-Fri week
    ended yesterday Friday).
    """
    # Sat-Fri week. Find the most recent past Friday, then back up 6 days
    # to its Saturday. On the cron's Sat run, the most recent past Friday
    # is yesterday, so last_sat = today - 7. Other days handled the same:
    #   Sat (wd=5): last_fri = today - 1,  last_sat = today - 7  ✓
    #   Sun (wd=6): last_fri = today - 2,  last_sat = today - 8  ✓
    #   Mon (wd=0): last_fri = today - 3,  last_sat = today - 9  ✓
    #   Fri (wd=4): we treat today's week as not yet complete and
    #               return the week before that — last_fri = today - 7.
    days_since_fri = (today.weekday() - 4) % 7
    if days_since_fri == 0:  # today is Friday — use the prior week
        days_since_fri = 7
    last_fri = today - timedelta(days=days_since_fri)
    last_sat = last_fri - timedelta(days=6)
    return last_sat, [last_sat + timedelta(days=i) for i in range(7)]


def _load_workdays(week_days):
    """Load workday_<yyyymmdd> entries for the given dates. Returns list of
    (date, entry) for those that exist."""
    found = []
    for d in week_days:
        eid = f'workday_{d.strftime("%Y%m%d")}'
        e = Entry.objects.filter(
            data__entry_id=eid, deleted_at__isnull=True,
        ).first()
        if e:
            found.append((d, e))
        else:
            logger.warning("workweek: missing %s", eid)
    return found


def _build_concat(workdays):
    """Stitch the dailies into one markdown string with day separators."""
    parts = []
    for d, e in workdays:
        header = f"# {d.strftime('%a %Y-%m-%d')}"
        parts.append(f"{header}\n\n{e.content.strip()}")
    return "\n\n---\n\n".join(parts)


def _upsert_entry(entry_id, content, tags):
    """Create or update an entry by data.entry_id."""
    existing = Entry.objects.filter(
        data__entry_id=entry_id, deleted_at__isnull=True,
    ).first()
    if existing:
        existing.content = content
        existing.timestamp_modified = time.time()
        existing.save(update_fields=['content', 'timestamp_modified'])
        logger.info("workweek: updated %s (%d chars)", entry_id, len(content))
        return existing
    result = services.create_entry(
        content=content,
        kind='memory',
        tags=tags,
        data={'entry_id': entry_id},
    )
    if isinstance(result, dict) and 'error' in result:
        raise RuntimeError(f"create_entry({entry_id}): {result['error']}")
    logger.info("workweek: created %s (%d chars)", entry_id, len(content))
    return result


def _call_claude(prompt):
    """Direct Anthropic API call. No MCP, no tools — pure summarization."""
    import anthropic
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
    client = anthropic.Anthropic(api_key=api_key)
    logger.info("workweek: calling Claude (claude-opus-4-6, %d char prompt)",
                len(prompt))
    response = client.messages.create(
        model='claude-opus-4-6',
        max_tokens=8000,
        messages=[{'role': 'user', 'content': prompt}],
    )
    if not response.content or not response.content[0].text:
        raise RuntimeError(f"Claude returned empty response: {response}")
    return response.content[0].text


def main():
    tz = get_timezone()
    today = datetime.now(tz).date()
    last_sat, week_days = _compute_prior_week(today)
    last_sat_str = last_sat.strftime('%Y%m%d')

    logger.info("workweek: building for %s..%s (last_sat=%s)",
                week_days[0].isoformat(), week_days[-1].isoformat(), last_sat_str)

    workdays = _load_workdays(week_days)
    if not workdays:
        logger.error("workweek: no workday entries found, aborting")
        sys.exit(1)
    logger.info("workweek: loaded %d/7 workday entries", len(workdays))

    concat = _build_concat(workdays)
    input_eid = f'workweek_input_{last_sat_str}'
    _upsert_entry(input_eid, concat, tags='workweek-input,fromai')

    prompt = f"{SUMMARY_INSTRUCTION}\n\n--- DAILY REPORTS ---\n\n{concat}"
    summary = _call_claude(prompt)

    start = week_days[0]
    end = week_days[-1]
    title = f"Workweek {start.isoformat()} ({start.strftime('%a %b %-d')} – {end.strftime('%a %b %-d')})"
    content = f"{title}\n\n{summary}"

    workweek_eid = f'workweek_{last_sat_str}'
    _upsert_entry(workweek_eid, content, tags='workweek-log,fromai')
    logger.info("workweek: done")


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        logger.error("workweek: fatal: %s\n%s", e, traceback.format_exc())
        sys.exit(1)
