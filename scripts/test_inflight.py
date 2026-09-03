#!/usr/bin/env python3
"""Functionality test for inflight todo parsing, item mutations, and the
three-way merge (docs/inflight.md). Pure functions, no database."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tjai_app import inflight  # noqa: E402

failures = []


def check(label, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + label + (('\n     ' + detail) if (detail and not cond) else ''))
    if not cond:
        failures.append(label)


DOC = """Port Simphony to Windows
Goal: MSVC build of the core packages.
Where it stands: PR open, two review comments.

## Live
- answer plexoos on CMake standard
  keep 17 for Ubuntu 22.04
- reply to ggalgoczi's two questions

## Done
. open PR 438

## Refs
- [PR 438](https://github.com/BNLNPPS/simphony/pull/438)
"""

p = inflight.parse(DOC)
check('title', p['title'] == 'Port Simphony to Windows')
check('description', p['description'].startswith('Goal:') and 'review comments.' in p['description'])
check('live items', [i['text'] for i in p['live']] == ['answer plexoos on CMake standard', "reply to ggalgoczi's two questions"])
check('continuation kept with item', p['live'][0]['lines'] == ['- answer plexoos on CMake standard', '  keep 17 for Ubuntu 22.04'])
check('done items', [i['text'] for i in p['done']] == ['open PR 438'])
check('refs', p['refs'].startswith('- [PR 438]'))
check('summary', inflight.summary(DOC) == {'open': 2, 'done': 1, 'title': 'Port Simphony to Windows'})

d = inflight.mark_done(DOC, 'answer plexoos on CMake standard')
pd = inflight.parse(d)
check('mark_done moves item', [i['text'] for i in pd['live']] == ["reply to ggalgoczi's two questions"]
      and [i['text'] for i in pd['done']] == ['open PR 438', 'answer plexoos on CMake standard'])
check('mark_done keeps continuation', pd['done'][1]['lines'][1] == '  keep 17 for Ubuntu 22.04')
check('mark_done leaves refs', inflight.parse(d)['refs'] == p['refs'])

r = inflight.reopen(d, 'open PR 438')
pr = inflight.parse(r)
check('reopen moves item to end of Live', [i['text'] for i in pr['live']] == ["reply to ggalgoczi's two questions", 'open PR 438'])

a = inflight.add_item(DOC, '  ask about   a Windows CI machine ')
pa = inflight.parse(a)
check('add_item appends normalized', pa['live'][-1]['text'] == 'ask about a Windows CI machine')
try:
    inflight.add_item(a, 'ask about a Windows CI machine'); check('add duplicate rejected', False)
except ValueError:
    check('add duplicate rejected', True)
try:
    inflight.mark_done(DOC, 'nope'); check('mark_done unknown rejected', False)
except ValueError:
    check('mark_done unknown rejected', True)

BARE = "Just a title\nSome description"
b1 = inflight.add_item(BARE, 'first item')
check('sections created on demand', inflight.parse(b1)['live'][0]['text'] == 'first item' and '## Live' in b1)
b2 = inflight.mark_done(b1, 'first item')
pb = inflight.parse(b2)
check('Done created after Live', pb['done'][0]['text'] == 'first item' and b2.index('## Live') < b2.index('## Done'))
b3 = inflight.add_item("Title\n\n## Refs\n- [x](y)", 'item')
check('Live inserted before Refs', b3.index('## Live') < b3.index('## Refs') and inflight.parse(b3)['live'][0]['text'] == 'item')

# three-way merge
base = DOC
ours = inflight.add_item(base, 'new item from the editor')           # editor adds an item
theirs = inflight.mark_done(base, 'answer plexoos on CMake standard')  # a session flips one done
m, conf = inflight.three_way_merge(base, ours, theirs)
pm = inflight.parse(m)
check('merge: both changes kept, no conflict', not conf
      and [i['text'] for i in pm['live']] == ["reply to ggalgoczi's two questions", 'new item from the editor']
      and [i['text'] for i in pm['done']] == ['open PR 438', 'answer plexoos on CMake standard'], m)
m2, conf2 = inflight.three_way_merge(base, base.replace('Goal: MSVC', 'Goal: MSVC and clang'), base.replace('Goal: MSVC', 'Goal: MSVC only'))
check('merge: same line both sides is a conflict', conf2 and '<<<<<<< yours' in m2 and '>>>>>>> server' in m2)
m3, conf3 = inflight.three_way_merge(base, theirs, theirs)
check('merge: identical changes collapse', not conf3 and m3 == theirs)
ours4 = inflight.add_item(base, 'A'); theirs4 = inflight.add_item(base, 'B')
m4, conf4 = inflight.three_way_merge(base, ours4, theirs4)
check('merge: two appends keep both', not conf4 and [i['text'] for i in inflight.parse(m4)['live']][-2:] == ['A', 'B'], m4)
ours5 = base.replace("- reply to ggalgoczi's two questions\n", '')
m5, conf5 = inflight.three_way_merge(base, ours5, theirs)
pm5 = inflight.parse(m5)
check('merge: our deletion survives their adjacent change', not conf5 and pm5['live'] == []
      and [i['text'] for i in pm5['done']] == ['open PR 438', 'answer plexoos on CMake standard'], m5)
ours7 = base.replace('- reply to ggalgoczi', '- reply to ggalgoczi promptly')
theirs7 = base.replace("- reply to ggalgoczi's two questions\n", "- reply to ggalgoczi's two questions\n- check CI\n")
m7, conf7 = inflight.three_way_merge(base, ours7, theirs7)
check('merge: edit next to their insertion, no conflict', not conf7 and 'promptly' in m7 and '- check CI' in m7, m7)
m6, conf6 = inflight.three_way_merge(base, base, theirs)
check('merge: unchanged ours takes theirs', not conf6 and m6 == theirs)

print(f"\n{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
