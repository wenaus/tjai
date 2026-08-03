#!/usr/bin/env python3
"""Capcom collector dispatcher — runs due poll sources (docs/capcom.md).

Runs as the capcom-dispatcher action's mechanical_script on a 10-minute
periodic tick. Reads the capcom_sources sysconfig registry, runs enabled
poll sources whose cadence has elapsed — or every enabled poll source when
the capcom_force_run sysconfig flag is set by the page's Update button —
records last-run times back in the registry, and purges notices past the
retention window. Listen sources are never touched here.
"""
import json
import logging
import subprocess
import sys
import time
import warnings
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup
import requests
from urllib3.exceptions import InsecureRequestWarning

from tjai_app import capcom
from tjai_app.db_log_handler import DbLogHandler
from tjai_app.models import SysConfig

logger = logging.getLogger('capcom')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S')
    _db = DbLogHandler(source='capcom')
    _db.setFormatter(_fmt)
    logger.addHandler(_db)
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)

PAX_EDEN_DIR = Path('/var/www/pax-eden')
PAX_EDEN_PYTHON = PAX_EDEN_DIR / '.venv/bin/python'
SWF_MONITOR_STATE_URL = (
    'https://localhost:18443/swf-monitor/api/capcom/state/'
)
SWF_MONITOR_HEADERS = {'Host': 'pandaserver02.sdcc.bnl.gov'}


def collect_eve_ahbazon(target_source=None):
    """Store the complete Ahbazon state payload supplied by Pax Eden."""
    code = (
        "import json; "
        "from pax_eden.gatecheck import capcom_ahbazon_state; "
        "print(json.dumps(capcom_ahbazon_state()))"
    )
    try:
        result = subprocess.run(
            [str(PAX_EDEN_PYTHON), '-c', code],
            cwd=PAX_EDEN_DIR,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f'Pax Eden gatecheck could not run: {e}') from e
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or 'no error output'
        raise RuntimeError(
            f'Pax Eden gatecheck exited {result.returncode}: {error}')
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f'Pax Eden gatecheck returned invalid JSON: {result.stdout}') from e

    capcom.set_state(**data)


def collect_swf_monitor(target_source=None):
    """Store every tile-exact state payload supplied by swf-monitor."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', InsecureRequestWarning)
            response = requests.get(
                SWF_MONITOR_STATE_URL,
                headers=SWF_MONITOR_HEADERS,
                timeout=30,
                verify=False,
            )
    except requests.RequestException as e:
        raise RuntimeError(f'swf-monitor state fetch failed: {e}') from e
    if response.status_code != 200:
        detail = response.text.strip()[:300] or 'no response body'
        raise RuntimeError(
            f'swf-monitor state fetch returned HTTP {response.status_code}: {detail}')
    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError('swf-monitor state fetch returned invalid JSON') from e

    states = data.get('states') if isinstance(data, dict) else None
    if not isinstance(states, list) or not states:
        raise RuntimeError('swf-monitor state payload has no states list')
    for entry in states:
        if not isinstance(entry, dict) or 'source' not in entry or 'value' not in entry:
            raise RuntimeError('swf-monitor returned an invalid state entry')
    selected = [
        entry for entry in states
        if target_source is None or entry['source'] == target_source
    ]
    if target_source is not None and not selected:
        raise RuntimeError(
            f'swf-monitor returned no state entry for {target_source!r}')
    for entry in selected:
        capcom.set_state(**entry)


# Poll collectors, keyed by registry source name. Each is a no-argument
# callable that polls its system and calls capcom.emit_notice()/set_state().
# Poll sources land one at a time (docs/capcom.md § Initial sources).
COLLECTORS = {
    'eve-ahbazon': collect_eve_ahbazon,
    'swf-monitor': collect_swf_monitor,
}


def _consume_force_target():
    """Read and clear capcom_force_run; return '*', one source, or None."""
    row = SysConfig.objects.filter(key='capcom_force_run').first()
    if row and row.value and row.value != '0':
        target = '*' if row.value == '1' else row.value
        row.value = '0'
        row.timestamp_modified = time.time()
        row.save(update_fields=['value', 'timestamp_modified'])
        return target
    return None


def run():
    force_target = _consume_force_target()
    sources = capcom.get_sources()
    if not sources:
        logger.info("capcom_dispatcher: registry empty, nothing to do")
        return

    now = time.time()
    poll_groups = {}
    for src in sources:
        if src.get('mode') != 'poll' or not src.get('enabled'):
            continue
        collector_name = src.get('collector') or src.get('source', '')
        poll_groups.setdefault(collector_name, []).append(src)

    ran = 0
    for collector_name, group in poll_groups.items():
        if force_target:
            if force_target != '*' and not any(
                    src.get('source') == force_target for src in group):
                continue
            due = True
        else:
            due = any(
                now >= (src.get('last_run') or 0) + (src.get('cadence') or 10) * 60
                for src in group
            )
        if not due:
            continue
        collector = COLLECTORS.get(collector_name)
        if collector is None:
            logger.warning(
                "capcom_dispatcher: enabled poll collector %r has no implementation",
                collector_name,
            )
            continue
        try:
            selected_source = (
                force_target if force_target and force_target != '*' else None)
            collector(selected_source)
            ran += 1
        except Exception as e:
            logger.error(
                "capcom_dispatcher: collector %r failed: %s", collector_name, e)
            capcom.emit_notice(
                source=group[0].get('source', collector_name), severity='warning',
                title=f"collector failed: {e}",
                dedup_key=f"capcom-collector-fail-{collector_name}",
            )
        for src in group:
            if not force_target or force_target == '*' \
                    or src.get('source') == force_target:
                src['last_run'] = now
    capcom.save_sources(sources)

    purged = capcom.purge_old_notices()
    logger.info("capcom_dispatcher: %d collector(s) run%s, %d notice(s) purged",
                ran, f" (forced {force_target})" if force_target else "", purged)


if __name__ == '__main__':
    run()
