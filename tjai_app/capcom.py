"""Capcom notice machinery — single write path, state tiles, source registry.

See docs/capcom.md. All notices enter through emit_notice(): the REST ingest
endpoint wraps it for posting systems and collectors; in-process emitters
call it directly.
"""
import json
import logging
import time
from datetime import timedelta

from django.utils import timezone

from .models import Notice, SysConfig

logger = logging.getLogger(__name__)

SEVERITIES = ('info', 'warning', 'alarm')
DEFAULT_RETENTION_DAYS = -1
TJAI_PIPELINE_SOURCE = 'tjai-pipeline'
TJAI_SYSTEM_SOURCE = 'tjai-system'


def emit_notice(source, title, severity='info', url='', dedup_key='', data=None):
    """Create a notice, threading onto an unarchived row with the same dedup_key.

    Returns the Notice row. Invalid severity is coerced to 'info' and logged.
    """
    if severity not in SEVERITIES:
        logger.error("emit_notice: unknown severity %r from source %s, using info",
                     severity, source)
        severity = 'info'
    now = timezone.now()
    if dedup_key:
        row = (Notice.objects.filter(dedup_key=dedup_key, archived=False)
               .order_by('-timestamp').first())
        if row:
            row.timestamp = now
            row.count += 1
            row.was_read = False
            row.title = title
            row.severity = severity
            if url:
                row.url = url
            if data:
                row.data = {**(row.data or {}), **data}
            row.save(update_fields=['timestamp', 'count', 'was_read', 'title',
                                    'severity', 'url', 'data'])
            return row
    return Notice.objects.create(
        timestamp=now, first_seen=now, source=source, severity=severity,
        title=title, url=url, dedup_key=dedup_key, data=data or {},
    )


def _get_json_config(key, default):
    """Read a sysconfig JSON value; surface parse errors, return default."""
    row = SysConfig.objects.filter(key=key).first()
    if not row or not row.value:
        return default
    try:
        return json.loads(row.value)
    except json.JSONDecodeError as e:
        logger.error("capcom: sysconfig %s holds invalid JSON: %s", key, e)
        return default


def _set_json_config(key, value):
    SysConfig.objects.update_or_create(
        key=key,
        defaults={'value': json.dumps(value), 'timestamp_modified': time.time()},
    )


def get_state():
    """Current tile values: {source: {value, color, updated}}."""
    return _get_json_config('capcom_state', {})


def set_state(source, value, color=None, url=None, updated=None):
    """Update a tile, retaining a source-supplied observation time."""
    states = get_state()
    entry = {
        'value': value,
        'updated': time.time() if updated is None else updated,
    }
    if color:
        entry['color'] = color
    if url:
        entry['url'] = url
    states[source] = entry
    _set_json_config('capcom_state', states)


def get_sources():
    """The source registry: list of {source, mode, cadence, enabled, last_run, note}."""
    return _get_json_config('capcom_sources', [])


def save_sources(sources):
    _set_json_config('capcom_sources', sources)


def add_pin_to_top(entry_id):
    """Append an entry to the hand-ordered top pin group, without duplicates."""
    order = _get_json_config('capcom_pin_order', [])
    if not isinstance(order, list):
        logger.error("capcom: capcom_pin_order is not a list: %r", order)
        order = []
    original_order = order
    order = list(dict.fromkeys(str(item) for item in order))
    entry_id = str(entry_id)
    if entry_id not in order:
        order.append(entry_id)
    if order != original_order:
        _set_json_config('capcom_pin_order', order)


def remove_pin_from_top(entry_id):
    """Remove an entry from the hand-ordered top pin group."""
    order = _get_json_config('capcom_pin_order', [])
    if not isinstance(order, list):
        logger.error("capcom: capcom_pin_order is not a list: %r", order)
        order = []
    original_order = order
    entry_id = str(entry_id)
    order = list(dict.fromkeys(
        str(item) for item in order if str(item) != entry_id
    ))
    if order != original_order:
        _set_json_config('capcom_pin_order', order)


def ensure_source(source, kind='feed', mode='listen', note=''):
    """Register an emitting source if it is not already in Capcom config."""
    sources = get_sources()
    if any(row.get('source') == source for row in sources):
        return
    sources.append({
        'source': source,
        'kind': kind,
        'mode': mode,
        'cadence': 10,
        'enabled': True,
        'last_run': 0,
        'note': note,
    })
    save_sources(sources)


def emit_tjai_notice(title, url='', dedup_key='', detail='', severity='info',
                     source=TJAI_PIPELINE_SOURCE):
    """Emit a curated notice from TJAI and ensure its source is registered."""
    note = ('TJAI system health transitions' if source == TJAI_SYSTEM_SOURCE
            else 'Completed TJAI products and curated terminal failures')
    ensure_source(source, note=note)
    data = {'detail': detail} if detail else None
    return emit_notice(
        source=source,
        title=title,
        severity=severity,
        url=url,
        dedup_key=dedup_key,
        data=data,
    )


def purge_old_notices():
    """Delete notices past a finite retention window; -1 keeps indefinitely."""
    row = SysConfig.objects.filter(key='capcom_retention_days').first()
    try:
        days = int(row.value) if row and row.value else DEFAULT_RETENTION_DAYS
    except ValueError:
        logger.error("capcom: capcom_retention_days is not an integer: %r", row.value)
        days = DEFAULT_RETENTION_DAYS
    if days < 0:
        return 0
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = Notice.objects.filter(timestamp__lt=cutoff).delete()
    if deleted:
        logger.info("capcom: purged %d notices older than %d days", deleted, days)
    return deleted
