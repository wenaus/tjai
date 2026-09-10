#!/usr/bin/env python3
"""tjai wrangler agent — durable-worker execution daemon (docs/wrangler.md).

Hosts a wrangle-ai Wrangler and Foreman over the tjai database: the Foreman
fires actions flagged ``data.runner='wrangler'`` from their existing schedule
vocabulary; workers are durable rows in ``wrangle_workers``; the web tier
enqueues on-demand work and rings with ``pg_notify``. Managed by supervisord
(``tjai-wrangler``). SIGTERM starts a graceful drain: in-flight workers finish,
then the process exits and supervisord restarts it on the new code.
"""
import os
import time

import bootstrap  # noqa: F401 - Django setup

from wrangle_ai import Foreman, Wrangler
from wrangle_ai.postgres import PgBell

from tjai_app.models import SysConfig
from tjai_app.wrangler import (
    BELL_CHANNEL, TjaiBullpen, TjaiRoster, build_dsn, handle_abort,
    handle_ai_dispatch, handle_capcom_refresh, handle_mechanical, logger,
    write_pulse,
)


def main():
    dsn = build_dsn()
    bullpen = TjaiBullpen(dsn, identity='tjai-wrangler')
    roster = TjaiRoster()
    bell = PgBell(dsn, channel=BELL_CHANNEL)
    foreman = Foreman(roster, bullpen, bell, name='tjai-foreman')
    wrangler = Wrangler(bullpen, bell, max_workers=4, idle_timeout=15.0,
                        name='tjai-wrangler', foreman=foreman, pulse=write_pulse)
    wrangler.register(
        'mechanical', handle_mechanical, timeout=3600.0,
        key_fn=lambda w: f"action:{w.payload.get('action_entry_id')}")
    # The handler only launches the agent and returns DETACHED; the doer's own
    # lifetime is bounded by the action's TJAI_AGENT_TIMEOUT, not by this.
    wrangler.register(
        'ai_dispatch', lambda w: handle_ai_dispatch(w, bullpen), timeout=600.0,
        key_fn=lambda w: f"action:{w.payload.get('action_entry_id')}")
    wrangler.register(
        'abort', lambda w: handle_abort(w, bullpen), timeout=60.0,
        key_fn=lambda w: f"abort:{w.payload.get('target_worker_id')}")
    wrangler.register(
        'capcom_refresh', handle_capcom_refresh, timeout=600.0,
        key_fn=lambda w: f"capcom:{w.payload.get('target')}")

    now = time.time()
    for key, value in (('wrangler_pid', str(os.getpid())),
                       ('wrangler_started', str(now))):
        SysConfig.objects.update_or_create(
            key=key, defaults={'value': value, 'timestamp_modified': now})
    logger.info("wrangler agent started (PID %d)", os.getpid())
    raise SystemExit(wrangler.run())


if __name__ == '__main__':
    main()
