"""Pre-save signal to snapshot entry state before modification."""
import contextvars
from contextlib import contextmanager

from django.db.models.signals import pre_save
from django.dispatch import receiver

from .models import Entry, create_entry_version

# Thread-safe context variable for changed_by attribution
_changed_by_var = contextvars.ContextVar('changed_by', default='web_ui')


@contextmanager
def entry_change_source(who):
    """Attribute versions created inside the block, then restore prior state."""
    token = _changed_by_var.set(who)
    try:
        yield
    finally:
        _changed_by_var.reset(token)


@receiver(pre_save, sender=Entry)
def snapshot_entry_before_save(sender, instance, **kwargs):
    """If an existing entry's content or data changed, save a version snapshot."""
    if not instance.pk:
        return  # new entry, nothing to snapshot

    try:
        old = Entry.objects.get(pk=instance.pk)
    except Entry.DoesNotExist:
        return

    # Only snapshot if content or substantive data changed.
    # Operational metadata keys (timestamps, run state, retry counters)
    # change frequently and are not worth versioning.
    _OPERATIONAL_KEYS = {
        'last_run', 'retry_after', 'retry_count', 'scheduled_time_config',
        'next_target', 'next_target_entry_id', 'pending_runs',
        'run_status', 'run_completed_at', 'run_exit_code', 'run_duration_seconds',
        'run_error', 'subagent_count', 'started_at',
    }
    old_data = old.data if isinstance(old.data, dict) else {}
    new_data = instance.data if isinstance(instance.data, dict) else {}
    content_changed = old.content != instance.content
    # Data changed = any non-operational key differs
    substantive_old = {k: v for k, v in old_data.items() if k not in _OPERATIONAL_KEYS}
    substantive_new = {k: v for k, v in new_data.items() if k not in _OPERATIONAL_KEYS}
    data_changed = substantive_old != substantive_new
    if not content_changed and not data_changed:
        return

    changed_by = _changed_by_var.get()

    if changed_by == 'autosave':
        return  # skip version for regular autosaves

    # first_autosave: fall through to create version (pre-edit snapshot)
    # web_ui: fall through to create version (manual save)

    create_entry_version(old, changed_by)
