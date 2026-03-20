"""Pre-save signal to snapshot entry state before modification."""
import time

from django.db.models.signals import pre_save
from django.dispatch import receiver

from .models import Entry, EntryVersion

# Thread-local or context hint for changed_by — set by views/services before saving
_changed_by = 'web_ui'


def set_changed_by(who):
    """Call before entry.save() to record who made the change."""
    global _changed_by
    _changed_by = who


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
    if old.content == instance.content and old_data == new_data:
        return

    global _changed_by
    if _changed_by == 'autosave':
        _changed_by = 'web_ui'
        return
    # Next version number: max existing + 1, or 1
    from django.db.models import Max
    max_num = EntryVersion.objects.filter(entry_id=old.pk).aggregate(Max('version_num'))['version_num__max'] or 0
    EntryVersion.objects.create(
        entry_id=old.pk,
        version_num=max_num + 1,
        content=old.content,
        data=old.data,
        changed_by=_changed_by,
        timestamp=time.time(),
    )
    _changed_by = 'web_ui'  # reset after use
