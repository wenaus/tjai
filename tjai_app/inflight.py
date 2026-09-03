"""Inflight todos: the working record of an activity (docs/inflight.md).

An inflight todo is a todo entry with status ``inflight``. Its body has a
fixed shape: the title line, a description, then the sections ``## Live``
(open items, lines starting ``- ``), ``## Done`` (finished items, lines
starting ``. ``), and ``## Refs`` (links). Indented lines continue the item
above them. This module parses that shape, applies the item mutations the
live view performs, and merges concurrent edits of one entry three-way.
"""
import re
from difflib import SequenceMatcher

STATUS = 'inflight'
LIVE, DONE, REFS = 'Live', 'Done', 'Refs'
_HEADING = re.compile(r'^##\s+(.+?)\s*$')
_OPEN = re.compile(r'^- (.*\S.*)$')
_DONE = re.compile(r'^\. (.*\S.*)$')


def is_inflight(entry):
    return entry.kind == 'todo' and entry.status == STATUS


CONFLICT_MARKERS = ('<<<<<<< yours', '=======', '>>>>>>> server')


def has_conflict(text):
    """True when a merge left conflict markers in the content."""
    return CONFLICT_MARKERS[0] in (text or '') and CONFLICT_MARKERS[2] in (text or '')


def fnv1a(text):
    """32-bit FNV-1a over code points; the editor computes the same in JS to
    name the content it loaded, so the save can find that exact version."""
    h = 0x811c9dc5
    for ch in text or '':
        h = ((h ^ ord(ch)) * 0x01000193) & 0xffffffff
    return h


def _norm(text):
    return '\n'.join(l.rstrip() for l in (text or '').split('\n'))


def _sections(lines):
    """Map section name -> (first body line, end exclusive); plus the
    description end (index of the first heading)."""
    sections, current, start, first_heading = {}, None, None, None
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if not m:
            continue
        if current is not None:
            sections[current] = (start, i)
        elif first_heading is None:
            first_heading = i
        current, start = m.group(1), i + 1
    if current is not None:
        sections[current] = (start, len(lines))
    return sections, (first_heading if first_heading is not None else len(lines))


def _items(lines, rng, rx):
    out = []
    if not rng:
        return out
    i, end = rng
    while i < end:
        m = rx.match(lines[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < end and lines[j].strip() and lines[j][0] in ' \t':
            j += 1
        out.append({'text': m.group(1).strip(), 'start': i, 'end': j,
                    'lines': lines[i:j]})
        i = j
    return out


def parse(content):
    lines = (content or '').split('\n')
    sections, desc_end = _sections(lines)
    refs = sections.get(REFS)
    return {
        'title': lines[0].strip() if lines else '',
        'description': '\n'.join(lines[1:desc_end]).strip('\n'),
        'live': _items(lines, sections.get(LIVE), _OPEN),
        'done': _items(lines, sections.get(DONE), _DONE),
        'refs': '\n'.join(lines[refs[0]:refs[1]]).strip('\n') if refs else '',
        'sections': sections,
        'lines': lines,
    }


def summary(content):
    p = parse(content)
    return {'open': len(p['live']), 'done': len(p['done']), 'title': p['title']}


def _ensure_section(lines, name):
    """Return lines with section `name` present. Live goes before Done,
    Done before Refs, each new section at the latest position allowed."""
    sections, _ = _sections(lines)
    if name in sections:
        return lines
    after = {LIVE: (DONE, REFS), DONE: (REFS,), REFS: ()}[name]
    insert_at = len(lines)
    for later in after:
        if later in sections:
            insert_at = min(insert_at, sections[later][0] - 1)   # the heading line
    head = ['## ' + name]
    if insert_at > 0 and lines[insert_at - 1].strip():
        head = [''] + head
    if insert_at == len(lines):
        return lines + head
    return lines[:insert_at] + head + [''] + lines[insert_at:]


def _append_to_section(lines, name, item_lines):
    """Insert item_lines after the last non-blank line of section `name`."""
    lines = _ensure_section(lines, name)
    start, end = _sections(lines)[0][name]
    at = end
    while at > start and not lines[at - 1].strip():
        at -= 1
    return lines[:at] + item_lines + lines[at:]


def _take(lines, item):
    """Remove an item's lines, and one blank line left dangling where a
    section became empty."""
    return lines[:item['start']] + lines[item['end']:]


def _find(items, text):
    text = (text or '').strip()
    for it in items:
        if it['text'] == text:
            return it
    return None


def mark_done(content, text):
    p = parse(content)
    item = _find(p['live'], text)
    if item is None:
        raise ValueError(f'no open item {text!r}')
    lines = _take(p['lines'], item)
    moved = ['. ' + item['text']] + item['lines'][1:]
    return '\n'.join(_append_to_section(lines, DONE, moved))


def reopen(content, text):
    p = parse(content)
    item = _find(p['done'], text)
    if item is None:
        raise ValueError(f'no done item {text!r}')
    lines = _take(p['lines'], item)
    moved = ['- ' + item['text']] + item['lines'][1:]
    return '\n'.join(_append_to_section(lines, LIVE, moved))


def add_item(content, text):
    text = ' '.join((text or '').split())
    if not text:
        raise ValueError('empty item')
    p = parse(content)
    if _find(p['live'], text) is not None:
        raise ValueError(f'item already open: {text!r}')
    return '\n'.join(_append_to_section(p['lines'], LIVE, ['- ' + text]))


def three_way_merge(base, ours, theirs):
    """Line-based three-way merge. Returns (merged_text, conflicted).

    Regions changed on one side only take that side; identical changes
    collapse; two insertions at the same point keep both (ours first); any
    other overlap is a conflict, written out with markers and reported.
    """
    a, b, c = _norm(base).split('\n'), _norm(ours).split('\n'), _norm(theirs).split('\n')
    hb = [op for op in SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != 'equal']
    hc = [op for op in SequenceMatcher(None, a, c, autojunk=False).get_opcodes() if op[0] != 'equal']
    out, conflicted, i, bi, ci = [], False, 0, 0, 0

    def apply(hunks, start, end):
        res, pos = [], start
        for i1, i2, repl in hunks:
            res.extend(a[pos:i1]); res.extend(repl); pos = i2
        res.extend(a[pos:end])
        return res

    while bi < len(hb) or ci < len(hc):
        starts = [h[1] for h in (hb[bi:bi + 1] + hc[ci:ci + 1])]
        start = min(starts)
        out.extend(a[i:start])
        end, rb, rc = start, [], []

        def joins(i1, i2):
            # A modification joins the region only when it overlaps it; an
            # insertion only when strictly inside it, or when both sides
            # insert at the same point. Touching changes stay separate.
            if i1 == i2:
                return (start < i1 < end) or (i1 == start == end)
            return i1 < end or (start == end and i1 == start)

        while True:
            took = False
            while bi < len(hb) and joins(hb[bi][1], hb[bi][2]):
                _t, i1, i2, j1, j2 = hb[bi]
                rb.append((i1, i2, b[j1:j2])); end = max(end, i2); bi += 1; took = True
            while ci < len(hc) and joins(hc[ci][1], hc[ci][2]):
                _t, i1, i2, j1, j2 = hc[ci]
                rc.append((i1, i2, c[j1:j2])); end = max(end, i2); ci += 1; took = True
            if not took:
                break
        if not rb and not rc:                       # first hunk starts here by construction
            h = hb[bi] if (bi < len(hb) and (ci >= len(hc) or hb[bi][1] <= hc[ci][1])) else hc[ci]
            raise RuntimeError(f'merge: no progress at {start} ({h})')
        if rb and rc:
            ob, oc = apply(rb, start, end), apply(rc, start, end)
            if ob == oc:
                out.extend(ob)
            elif start == end:
                out.extend(ob); out.extend(oc)
            else:
                conflicted = True
                out.append('<<<<<<< yours'); out.extend(ob)
                out.append('======='); out.extend(oc)
                out.append('>>>>>>> server')
        elif rb:
            out.extend(apply(rb, start, end))
        else:
            out.extend(apply(rc, start, end))
        i = end
    out.extend(a[i:])
    return '\n'.join(out), conflicted
