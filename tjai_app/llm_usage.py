"""Structured accounting for TJAI-launched subscription Codex processes."""

import logging
import re
from collections import defaultdict
from datetime import timedelta


_CODEX_TOKENS_RE = re.compile(
    r'(?:^|\n)tokens used\s*\n\s*([\d,]+)\s*(?:\n|$)',
    re.IGNORECASE,
)


def parse_codex_total_tokens(output):
    """Return the Codex CLI total-token footer, or None when unavailable."""
    matches = _CODEX_TOKENS_RE.findall(output or '')
    if not matches:
        return None
    return int(matches[-1].replace(',', ''))


def record_codex_usage(*, action_id, model, effort, exit_code, run_status,
                       duration_sec=None, output='', entry_id=None,
                       tracking=None):
    """Write one AppLog event for a completed TJAI Codex subprocess."""
    from .db_log_handler import DbLogHandler

    logger = logging.getLogger('tjai_llm_usage')
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(DbLogHandler(source='llm_usage'))

    total_tokens = parse_codex_total_tokens(output)
    usage_reported = total_tokens is not None
    token_text = f'{total_tokens:,} tokens' if usage_reported else 'usage unreported'
    extra = {
        'event': 'llm_usage',
        'action_id': action_id,
        'provider': 'openai',
        'runner': 'codex',
        'billing': 'subscription',
        'model': model,
        'effort': effort,
        'exit_code': exit_code,
        'run_status': run_status,
        'usage_reported': usage_reported,
    }
    if duration_sec is not None:
        extra['duration_sec'] = duration_sec
    if total_tokens is not None:
        extra['total_tokens'] = total_tokens
    if entry_id:
        extra['entry_id'] = entry_id
    if tracking:
        extra['tracking'] = tracking
    logger.info(
        'llm_usage %s: %s, %s, exit=%s, model=%s, effort=%s',
        action_id, token_text, run_status, exit_code, model, effort,
        extra=extra,
    )


def summarize_codex_usage(events, now):
    """Build 24-hour and 7-day totals and per-action breakdowns."""
    windows = {
        '24h': now - timedelta(hours=24),
        '7d': now - timedelta(days=7),
    }

    def new_bucket():
        return {
            'runs': 0,
            'reported_runs': 0,
            'total_tokens': 0,
            'unreported_runs': 0,
            'timeouts': 0,
        }

    totals = {name: new_bucket() for name in windows}
    actions = defaultdict(lambda: {
        '24h': new_bucket(),
        '7d': new_bucket(),
        'models': set(),
        'efforts': set(),
    })

    def add(bucket, event):
        bucket['runs'] += 1
        if event.get('usage_reported') and event.get('total_tokens') is not None:
            bucket['reported_runs'] += 1
            bucket['total_tokens'] += int(event['total_tokens'])
        else:
            bucket['unreported_runs'] += 1
        if event.get('exit_code') == 124:
            bucket['timeouts'] += 1

    for event in events:
        timestamp = event.get('timestamp')
        if timestamp is None:
            continue
        action_id = event.get('action_id') or 'unknown'
        action = actions[action_id]
        if event.get('model'):
            action['models'].add(event['model'])
        if event.get('effort'):
            action['efforts'].add(event['effort'])
        for name, cutoff in windows.items():
            if timestamp >= cutoff:
                add(totals[name], event)
                add(action[name], event)

    def finish(bucket):
        bucket['avg_tokens'] = (
            round(bucket['total_tokens'] / bucket['reported_runs'])
            if bucket['reported_runs'] else None
        )

    for bucket in totals.values():
        finish(bucket)

    action_rows = []
    for action_id, action in actions.items():
        if action['7d']['runs'] == 0:
            continue
        finish(action['24h'])
        finish(action['7d'])
        action_rows.append({
            'action_id': action_id,
            'model': ', '.join(sorted(action['models'])) or '-',
            'effort': ', '.join(sorted(action['efforts'])) or '-',
            '24h': action['24h'],
            '7d': action['7d'],
        })
    action_rows.sort(
        key=lambda row: (row['7d']['total_tokens'], row['7d']['runs']),
        reverse=True,
    )
    return {'windows': totals, 'actions': action_rows}
