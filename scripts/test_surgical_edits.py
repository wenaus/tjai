"""Test the surgical edit services: replace_text_in_entry and
replace_section_in_entry. Creates a throwaway test entry, runs each
case, cleans up. Bash/python style — no pytest framework.

Usage:
    cd /home/admin/github/tjrepo/tjai/scripts
    ./test_surgical_edits.py
or
    /home/admin/github/tjrepo/tjai/.venv/bin/python test_surgical_edits.py
"""
import bootstrap  # noqa: F401
import sys
import time

from tjai_app.models import Entry, Context
from tjai_app import services


PASS = 0
FAIL = 0
ERRORS = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")
        ERRORS.append((label, detail))


def make_entry(content):
    """Create a fresh test entry, return (entry_id, modified_iso)."""
    ctx, _ = Context.objects.get_or_create(name='tjai', defaults={'description': 'tjai dev'})
    res = services.create_entry(
        content=content,
        kind='memory',
        context='tjai',
        tags='surgical-edit-test',
        data={'entry_id': f'surgical-test-{int(time.time()*1000)}'},
    )
    assert 'error' not in res, res
    return res['id'], res['modified']


def cleanup(entry_id):
    Entry.objects.filter(id=entry_id).update(deleted_at=time.time())


# ─────────────────────────────────────────────────────────────────────
print("\n[group] replace_text_in_entry")
# ─────────────────────────────────────────────────────────────────────

# 1. Happy path — single occurrence, exact replacement
eid, mod = make_entry("alpha beta gamma\nfoo bar baz quux\nend.")
r = services.replace_text_in_entry(eid, "bar baz", "BAR-BAZ")
check("happy: replaces single occurrence",
      'error' not in r and 'BAR-BAZ' in r['content'] and 'bar baz' not in r['content'],
      str(r))
check("happy: returns replaced_count=1",
      r.get('replaced_count') == 1, str(r.get('replaced_count')))
cleanup(eid)

# 2. NO_MATCH
eid, _ = make_entry("alpha beta gamma\nfoo bar baz")
r = services.replace_text_in_entry(eid, "MISSING", "X")
check("NO_MATCH on absent text", r.get('code') == 'NO_MATCH', str(r))
cleanup(eid)

# 3. MULTIPLE_MATCHES without replace_all
eid, _ = make_entry("foo and foo and foo")
r = services.replace_text_in_entry(eid, "foo", "BAR")
check("MULTIPLE_MATCHES on ambiguous text",
      r.get('code') == 'MULTIPLE_MATCHES' and r.get('count') == 3, str(r))
cleanup(eid)

# 4. replace_all=True
eid, _ = make_entry("foo and foo and foo")
r = services.replace_text_in_entry(eid, "foo", "BAR", replace_all=True)
check("replace_all replaces every occurrence",
      'error' not in r and r['content'] == "BAR and BAR and BAR" and r['replaced_count'] == 3,
      str(r))
cleanup(eid)

# 5. STALE_PRECONDITION
eid, mod = make_entry("alpha beta gamma\nfoo bar baz")
# Bump it to make our captured `mod` stale
services.replace_text_in_entry(eid, "alpha", "ALPHA")
r = services.replace_text_in_entry(eid, "beta", "BETA", expected_modified_at=mod)
check("STALE_PRECONDITION when modified_at differs",
      r.get('code') == 'STALE_PRECONDITION'
      and r.get('expected_modified_at') == mod
      and r.get('current_modified_at') != mod,
      str(r))
cleanup(eid)

# 6. expected_modified_at matches → succeeds
eid, mod = make_entry("alpha beta gamma\nfoo bar baz")
r = services.replace_text_in_entry(eid, "beta", "BETA", expected_modified_at=mod)
check("matching expected_modified_at allows write",
      'error' not in r and 'BETA' in r['content'], str(r))
cleanup(eid)

# 7. EMPTY_RESULT (replacing the whole content with empty)
eid, _ = make_entry("complete payload, will be wiped")
r = services.replace_text_in_entry(eid, "complete payload, will be wiped", "")
check("EMPTY_RESULT when replacement empties content",
      r.get('code') == 'EMPTY_RESULT', str(r))
cleanup(eid)

# 8. NOT_FOUND
r = services.replace_text_in_entry("00000000-0000-0000-0000-000000000000", "foo", "bar")
check("NOT_FOUND on missing entry", r.get('code') == 'NOT_FOUND', str(r))

# 9. BAD_REQUEST
r = services.replace_text_in_entry("", "foo", "bar")
check("BAD_REQUEST on empty entry_id", r.get('code') == 'BAD_REQUEST', str(r))
r = services.replace_text_in_entry("abc", "", "bar")
check("BAD_REQUEST on empty old_text", r.get('code') == 'BAD_REQUEST', str(r))
r = services.replace_text_in_entry("abc", "foo", None)
check("BAD_REQUEST on None new_text", r.get('code') == 'BAD_REQUEST', str(r))

# 10. NOOP_PATCH on identical old/new (catches "silent succeed when did nothing")
eid, _ = make_entry("alpha beta gamma\nfoo bar baz")
r = services.replace_text_in_entry(eid, "beta", "beta")
check("NOOP_PATCH when old_text == new_text",
      r.get('code') == 'NOOP_PATCH', str(r))
cleanup(eid)

# 10b. NOOP_PATCH also fires under replace_all
eid, _ = make_entry("foo and foo and foo")
r = services.replace_text_in_entry(eid, "foo", "foo", replace_all=True)
check("NOOP_PATCH under replace_all when old==new",
      r.get('code') == 'NOOP_PATCH', str(r))
cleanup(eid)


# ─────────────────────────────────────────────────────────────────────
print("\n[group] replace_section_in_entry")
# ─────────────────────────────────────────────────────────────────────

DOC = """# Title

intro paragraph.

## Foo

old foo body line 1
old foo body line 2

## Bar

bar body unchanged

### Bar subhead

deep stuff here

## Foo

second foo body
"""

# 10. Happy path — replace unique section. Heading 'Bar' is unique.
eid, _ = make_entry(DOC)
r = services.replace_section_in_entry(eid, "Bar", "\nNEW BAR BODY\n")
check("section happy: heading line preserved",
      'error' not in r and "## Bar" in r['content'], str(r)[:200])
check("section happy: old body gone",
      'error' not in r and "bar body unchanged" not in r['content'], str(r)[:200])
check("section happy: new body present",
      'error' not in r and "NEW BAR BODY" in r['content'], str(r)[:200])
check("section happy: stops at next heading at SAME level (## Foo preserved)",
      'error' not in r and "second foo body" in r['content'], str(r)[:200])
check("section happy: deeper subhead INSIDE section was replaced too",
      'error' not in r and "Bar subhead" not in r['content'] and "deep stuff" not in r['content'],
      str(r)[:200])
cleanup(eid)

# 11. HEADING_NOT_FOUND
eid, _ = make_entry(DOC)
r = services.replace_section_in_entry(eid, "Nonexistent", "x")
check("HEADING_NOT_FOUND", r.get('code') == 'HEADING_NOT_FOUND', str(r))
cleanup(eid)

# 12. MULTIPLE_HEADINGS without occurrence
eid, _ = make_entry(DOC)
r = services.replace_section_in_entry(eid, "Foo", "x")
check("MULTIPLE_HEADINGS when 'Foo' appears twice",
      r.get('code') == 'MULTIPLE_HEADINGS' and r.get('count') == 2, str(r))
cleanup(eid)

# 13. occurrence=1 picks first
eid, _ = make_entry(DOC)
r = services.replace_section_in_entry(eid, "Foo", "FIRST FOO REPLACED", occurrence=1)
check("occurrence=1 replaces first 'Foo' section",
      'error' not in r and "FIRST FOO REPLACED" in r['content']
      and "second foo body" in r['content'],
      str(r)[:200])
cleanup(eid)

# 14. occurrence=2 picks second
eid, _ = make_entry(DOC)
r = services.replace_section_in_entry(eid, "Foo", "SECOND FOO REPLACED", occurrence=2)
check("occurrence=2 replaces second 'Foo' section",
      'error' not in r and "SECOND FOO REPLACED" in r['content']
      and "old foo body line 1" in r['content'],
      str(r)[:200])
cleanup(eid)

# 15. OCCURRENCE_OUT_OF_RANGE
eid, _ = make_entry(DOC)
r = services.replace_section_in_entry(eid, "Foo", "x", occurrence=99)
check("OCCURRENCE_OUT_OF_RANGE on too-high index",
      r.get('code') == 'OCCURRENCE_OUT_OF_RANGE', str(r))
cleanup(eid)

# 16. level disambiguates between same text at different levels
DOC2 = """# Foo

top-level foo body

## Foo

second-level foo body
"""
eid, _ = make_entry(DOC2)
r = services.replace_section_in_entry(eid, "Foo", "L2-NEW", level=2)
check("level=2 picks the ## Foo, leaves # Foo alone",
      'error' not in r and "L2-NEW" in r['content']
      and "top-level foo body" in r['content']
      and "second-level foo body" not in r['content'],
      str(r)[:300])
cleanup(eid)

# 17. Headings inside fenced code blocks are ignored
DOC3 = """# Real

real body

```
## Fake

fake body
```

## Other

other body
"""
eid, _ = make_entry(DOC3)
r = services.replace_section_in_entry(eid, "Fake", "x")
check("HEADING_NOT_FOUND when only match is inside ``` fence",
      r.get('code') == 'HEADING_NOT_FOUND', str(r))
cleanup(eid)

# 18. STALE_PRECONDITION on section
eid, mod = make_entry(DOC)
services.replace_text_in_entry(eid, "intro paragraph", "INTRO")
r = services.replace_section_in_entry(eid, "Bar", "x", expected_modified_at=mod)
check("section STALE_PRECONDITION", r.get('code') == 'STALE_PRECONDITION', str(r))
cleanup(eid)

# 18b. NOOP_PATCH on section when new_body equals existing body
# Use a flat doc so the body bytes are unambiguous: section "B" body is the
# lines between "## B" and "## C" — i.e. ["", "body B", ""] joined → "\nbody B\n".
DOC_FLAT = "# A\n\nbody A\n\n## B\n\nbody B\n\n## C\n\nbody C\n"
eid, _ = make_entry(DOC_FLAT)
r = services.replace_section_in_entry(eid, "B", "\nbody B\n")
check("section NOOP_PATCH when new_body matches existing",
      r.get('code') == 'NOOP_PATCH', str(r))
cleanup(eid)

# 19. Versioning — surgical edit creates a new version (timestamp_modified bumps)
eid, mod_before = make_entry("alpha beta gamma\nfoo bar baz")
time.sleep(0.05)  # ensure timestamp_modified changes
r = services.replace_text_in_entry(eid, "beta", "BETA")
check("surgical edit bumps modified timestamp",
      'error' not in r and r['modified'] != mod_before, f"before={mod_before} after={r.get('modified')}")
cleanup(eid)


# ─────────────────────────────────────────────────────────────────────
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
