"""Structured operational logging helpers for tool-like calls.

Uses the existing AppLog pipeline via DbLogHandler.  The stable
`applog_call` marker is intentionally present in both message text and
extra_data so logs are easy to find by text search or structured filters.
"""

import json
import logging
import time


SECRET_KEY_PARTS = ('key', 'token', 'secret', 'password', 'bearer')
ARGS_PREVIEW_CHARS = 2000


def _redact(value):
    if isinstance(value, dict):
        return {
            k: ('[REDACTED]' if any(p in str(k).lower() for p in SECRET_KEY_PARTS)
                else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _preview_json(value, limit=ARGS_PREVIEW_CHARS):
    try:
        text = json.dumps(_redact(value), sort_keys=True, default=str)
    except Exception:
        text = repr(value)
    if len(text) > limit:
        return text[:limit] + f'... [TRUNCATED args preview from {len(text)} chars]'
    return text


def applog_call(
    logger,
    *,
    call_type,
    tool,
    caller=None,
    model=None,
    action_id=None,
    entry_id=None,
    tool_call_id=None,
    iteration=None,
    args=None,
    ok=True,
    error=None,
    is_error=None,
    duration_ms=None,
    result_chars=None,
    returned_chars=None,
    result_count=None,
    truncated=False,
    truncation_limit=None,
    level=None,
):
    """Emit one structured AppLog call event through an existing logger."""
    if level is None:
        level = logging.ERROR if (not ok or error or is_error) else logging.INFO
    args_preview = _preview_json(args or {})

    bits = [
        'applog_call',
        str(call_type),
        str(tool),
        f'ok={bool(ok)}',
    ]
    if model:
        bits.append(f'model={model}')
    if iteration is not None:
        bits.append(f'iter={iteration}')
    if result_chars is not None:
        if returned_chars is not None and returned_chars != result_chars:
            bits.append(f'result={returned_chars}/{result_chars} chars')
        else:
            bits.append(f'result={result_chars} chars')
    if result_count is not None:
        bits.append(f'count={result_count}')
    if duration_ms is not None:
        bits.append(f'duration_ms={duration_ms}')
    if truncated:
        bits.append('truncated')
    if error:
        bits.append(f'error={str(error)[:300]}')
    bits.append(f'args={args_preview[:500]}')

    extra = {
        'event': 'applog_call',
        'call_type': call_type,
        'tool': tool,
        'caller': caller,
        'model': model,
        'action_id': action_id,
        'entry_id': entry_id,
        'tool_call_id': tool_call_id,
        'iteration': iteration,
        'args_preview': args_preview,
        'ok': bool(ok),
        'error': str(error) if error else None,
        'is_error': bool(is_error) if is_error is not None else None,
        'duration_ms': duration_ms,
        'result_chars': result_chars,
        'returned_chars': returned_chars,
        'result_count': result_count,
        'truncated': bool(truncated),
        'truncation_limit': truncation_limit,
    }
    logger.log(level, ' '.join(bits), extra=extra)


class CallTimer:
    def __init__(self):
        self.started = time.monotonic()

    def ms(self):
        return int((time.monotonic() - self.started) * 1000)
