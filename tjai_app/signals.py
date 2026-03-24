"""Pre-save signal to snapshot entry state before modification."""
import contextvars
import time

from django.db.models.signals import pre_save
from django.dispatch import receiver

from .models import Entry, EntryVersion

# Thread-safe context variable for changed_by attribution
_changed_by_var = contextvars.ContextVar('changed_by', default='web_ui')


def set_changed_by(who):
    """Call before entry.save() to record who made the change."""
    _changed_by_var.set(who)


@receiver(pre_save, sender=Entry)
def snapshot_entry_before_save(sender, instance, **kwargs):
    """If an existing entry's content or data changed, save a version snapshot."""
    if not instance.pk:
        return  # new entry, nothing to snapshot

    try:
        old = Entry.objects.get(pk=instance.pk)
    except Entry.DoesNotExist:
        return

    # Only snapshot if content or data actually changed
    old_data = old.data if isinstance(old.data, dict) else {}
    new_data = instance.data if isinstance(instance.data, dict) else {}
    content_changed = old.content != instance.content
    data_changed = old_data != new_data
    if not content_changed and not data_changed:
        return

    changed_by = _changed_by_var.get()
    _changed_by_var.set('web_ui')  # always reset immediately

    if changed_by == 'autosave':
        return  # skip version for regular autosaves

    # first_autosave: fall through to create version (pre-edit snapshot)
    # web_ui: fall through to create version (manual save)

    from django.db.models import Max
    max_num = EntryVersion.objects.filter(entry_id=old.pk).aggregate(Max('version_num'))['version_num__max'] or 0
    EntryVersion.objects.create(
        entry_id=old.pk,
        version_num=max_num + 1,
        content=old.content,
        data=old.data,
        changed_by=changed_by,
        timestamp=time.time(),
    )
