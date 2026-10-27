#!/usr/bin/env python3
"""Import authorized sent/received TeamComms history into the personal dialog view.

Uses the existing connector's authenticated read command. No message publication,
session enrollment, receipt mutation or native transcript edit occurs.
"""

import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time

import bootstrap  # noqa: F401

from django.db import transaction

from tjai_app.dialog_people import SYNC_KEY, sync_state
from tjai_app.models import SysConfig
from tjai_app.teamcomms_dialog import person, project_message, refresh_exchange_people


def save_state(state):
    SysConfig.objects.update_or_create(key=SYNC_KEY, defaults={
        'value': json.dumps(state), 'timestamp_modified': time.time(),
    })


def read_page(args, cursor):
    query = {'direction': 'all', 'limit': 100}
    if cursor:
        query['cursor'] = cursor
    result = subprocess.run([
        args.connector_python, '-m', 'teamcomms.connectors.cli',
        '--config', str(args.config), 'call', 'get_message_history', json.dumps(query),
    ], capture_output=True, text=True, timeout=45)
    if result.returncode:
        # The connector handles credential loading. Do not persist its process
        # environment, request headers or arbitrary response bodies in TJAI.
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f'TeamComms history read failed (exit {result.returncode})')
    page = json.loads(result.stdout)
    if page.get('error') or page.get('schema_version') != 1:
        raise ValueError('TeamComms history returned an error or unsupported schema')
    if (not isinstance(page.get('messages'), list) or len(page['messages']) > 100
            or not isinstance(page.get('has_more'), bool)):
        raise ValueError('TeamComms history has an invalid page shape')
    if not isinstance(page.get('next_cursor'), str) or not page['next_cursor']:
        raise ValueError('TeamComms history did not return a continuation cursor')
    if page['messages'] and cursor == page['next_cursor']:
        raise ValueError('TeamComms history cursor did not advance')
    return page


def sync(args):
    source = json.loads(args.config.read_text())['url'].rstrip('/')
    state = sync_state()
    if state.get('source') and state['source'] != source:
        raise ValueError('TeamComms dialog is already bound to another source')
    added = 0
    for _ in range(args.max_pages):
        page = read_page(args, state.get('cursor'))
        viewer = page['viewer']
        owner_id, _ = person(viewer)
        if (state.get('viewer_participant_id')
                and state['viewer_participant_id'] != viewer['participant_id']):
            raise ValueError('TeamComms dialog credential changed participant identity')
        with transaction.atomic():
            for message in page['messages']:
                relation = message['viewer_relation']
                if not (relation['sent'] or relation['received']):
                    raise ValueError('TeamComms history contains an unrelated message')
                added += project_message(source, message, message['author_identity'],
                    [recipient['identity'] for recipient in message['recipients']],
                    message.get('sender_session'), local_person_id=owner_id)
            total = refresh_exchange_people()
            state = {**state, 'source': source, 'cursor': page['next_cursor'],
                     'viewer_participant_id': viewer['participant_id'],
                     'local_person_id': owner_id, 'last_success': time.time(),
                     'complete': not page['has_more'], 'messages': total, 'error': None}
            save_state(state)
        if not page['has_more']:
            break
    print(json.dumps({'added': added, 'messages': state['messages'], 'complete': state['complete']}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path,
                        default=Path.home() / '.config/teamcomms/torre-ai/connector.json')
    parser.add_argument('--connector-python', default=str(Path.home() / 'teamcomms-venv/bin/python'))
    parser.add_argument('--max-pages', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 1000:
        parser.error('--max-pages must be between 1 and 1000')
    lock = Path.home() / '.cache/tjai/teamcomms-dialog.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('TeamComms dialog sync already running')
            return
        try:
            sync(args)
        except Exception as exc:
            state = sync_state()
            save_state({**state, 'error': type(exc).__name__, 'last_error': time.time()})
            raise


if __name__ == '__main__':
    main()
