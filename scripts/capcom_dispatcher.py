#!/usr/bin/env python3
"""Capcom collector dispatcher — runs due poll sources (docs/capcom.md).

Runs as the capcom-dispatcher action's mechanical_script on a 10-minute
periodic tick. Reads the capcom_sources sysconfig registry, runs enabled
poll sources whose cadence has elapsed, records scheduled last-run times
back in the registry, and purges notices past the retention window. The
action agent may also call run(force_target=...) for an out-of-band manual
refresh; that never changes the periodic schedule. Listen sources are never
touched here.
"""
import json
import logging
import os
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
CORUN_DIR = Path('/var/www/corun-ai')
CORUN_PYTHON = CORUN_DIR / '.venv/bin/python'
SWF_MONITOR_STATE_URL = (
    'https://localhost:18443/swf-monitor/api/capcom/state/'
)
SWF_MONITOR_USER_STATE_URL = (
    'https://localhost:18443/swf-monitor/api/capcom/user-state/'
)
SWF_MONITOR_HEADERS = {'Host': 'pandaserver02.sdcc.bnl.gov'}
SWF_MONITOR_USERNAME = os.environ.get('CAPCOM_SWF_USERNAME', 'wenauseic')


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


def _fetch_swf_monitor_states(url, params=None):
    """Fetch and validate one swf-monitor Capcom state response."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', InsecureRequestWarning)
            response = requests.get(
                url,
                headers=SWF_MONITOR_HEADERS,
                params=params,
                timeout=30,
                verify=False,
            )
    except requests.RequestException as e:
        raise RuntimeError(f'swf-monitor state fetch failed: {e}') from e
    if response.status_code != 200:
        raise RuntimeError(f'swf-monitor returned HTTP {response.status_code}')
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
    return states


def collect_swf_monitor(target_source=None):
    """Store global and configured-user state supplied by swf-monitor."""
    states = _fetch_swf_monitor_states(SWF_MONITOR_STATE_URL)
    states.extend(_fetch_swf_monitor_states(
        SWF_MONITOR_USER_STATE_URL,
        params={'username': SWF_MONITOR_USERNAME},
    ))
    selected = [
        entry for entry in states
        if target_source is None or entry['source'] == target_source
    ]
    if target_source is not None and not selected:
        raise RuntimeError(
            f'swf-monitor returned no state entry for {target_source!r}')
    for entry in selected:
        capcom.set_state(**entry)


def collect_corun_ai(target_source=None):
    """Store every tile-exact state payload supplied by corun-ai."""
    code = (
        "import json, os, sys; "
        "sys.path.insert(0, 'src'); "
        "os.environ['DJANGO_SETTINGS_MODULE'] = 'corun_project.settings'; "
        "import django; django.setup(); "
        "from corun_app.capcom import capcom_states; "
        "print(json.dumps(capcom_states()))"
    )
    try:
        result = subprocess.run(
            [str(CORUN_PYTHON), '-c', code],
            cwd=CORUN_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f'corun-ai state could not run: {e}') from e
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or 'no error output'
        raise RuntimeError(
            f'corun-ai state exited {result.returncode}: {error}')
    try:
        states = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f'corun-ai state returned invalid JSON: {result.stdout}') from e
    if not isinstance(states, list) or not states:
        raise RuntimeError('corun-ai state payload has no states list')
    for entry in states:
        if not isinstance(entry, dict) or 'source' not in entry or 'value' not in entry:
            raise RuntimeError('corun-ai returned an invalid state entry')
    selected = [
        entry for entry in states
        if target_source is None or entry['source'] == target_source
    ]
    if target_source is not None and not selected:
        raise RuntimeError(
            f'corun-ai returned no state entry for {target_source!r}')
    for entry in selected:
        capcom.set_state(**entry)


# Poll collectors, keyed by registry source name. Each is a no-argument
# callable that polls its system and calls capcom.emit_notice()/set_state().
# Poll sources land one at a time (docs/capcom.md § Initial sources).
COLLECTORS = {
    'corun-ai': collect_corun_ai,
    'eve-ahbazon': collect_eve_ahbazon,
    'swf-monitor': collect_swf_monitor,
}


def run(force_target=None):
    """Run due collectors, or one/all collectors without shifting cadence."""
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
        if not force_target:
            for src in group:
                src['last_run'] = now
    capcom.save_sources(sources)

    purged = capcom.purge_old_notices()
    logger.debug("capcom_dispatcher: %d collector(s) run%s, %d notice(s) purged",
                 ran, f" (forced {force_target})" if force_target else "", purged)


if __name__ == '__main__':
    run()
