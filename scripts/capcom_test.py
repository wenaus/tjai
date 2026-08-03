#!/usr/bin/env python3
"""Functionality test for Capcom notice machinery (docs/capcom.md).

Exercises emit/threading/read/archive/purge against the live database using
a dedicated test source, then removes every row it created. Exit 0 on pass.
"""
import sys

import bootstrap  # noqa: F401 - Django setup

from tjai_app import capcom
from tjai_app.models import Notice

SOURCE = 'capcom-test'
failures = []


def check(label, cond):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond:
        failures.append(label)


def run():
    Notice.objects.filter(source=SOURCE).delete()

    n1 = capcom.emit_notice(SOURCE, 'first event', url='https://example.com',
                            dedup_key='ct-1')
    check('emit creates row', Notice.objects.filter(source=SOURCE).count() == 1)
    check('new notice unread', n1.was_read is False and n1.count == 1)

    n2 = capcom.emit_notice(SOURCE, 'first event again', dedup_key='ct-1')
    check('same dedup_key threads, not inserts',
          Notice.objects.filter(source=SOURCE).count() == 1)
    check('thread bumps count', n2.id == n1.id and n2.count == 2)
    check('thread keeps url when update has none', n2.url == 'https://example.com')

    Notice.objects.filter(id=n1.id).update(was_read=True)
    n3 = capcom.emit_notice(SOURCE, 'third', dedup_key='ct-1')
    check('thread resets was_read', n3.was_read is False)

    Notice.objects.filter(id=n1.id).update(archived=True)
    n4 = capcom.emit_notice(SOURCE, 'after archive', dedup_key='ct-1')
    check('archived row not threaded onto', n4.id != n1.id)

    n5 = capcom.emit_notice(SOURCE, 'no dedup')
    n6 = capcom.emit_notice(SOURCE, 'no dedup')
    check('empty dedup_key never threads', n5.id != n6.id)

    bad = capcom.emit_notice(SOURCE, 'bad severity', severity='bogus')
    check('invalid severity coerced to info', bad.severity == 'info')

    before = Notice.objects.count()
    capcom.purge_old_notices()
    check('purge leaves fresh notices', Notice.objects.count() == before)

    deleted, _ = Notice.objects.filter(source=SOURCE).delete()
    print(f"cleaned up {deleted} test row(s)")

    if failures:
        print(f"{len(failures)} FAILURE(S)")
        return 1
    print("all checks passed")
    return 0


if __name__ == '__main__':
    sys.exit(run())
