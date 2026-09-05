"""Captures: images stashed from mail by the Gmail add-on (docs/addons.md).

A capture is one memory entry tagged ``capture`` plus its image files under
``data/captures/YYYY-MM/<entry uuid>/``. The entry content carries the
subject, sender, Gmail permalink, the note, and one markdown image line per
file. The files and the Captures page are public reads: a session on any
machine fetches them with a plain GET of the URL.
"""
import logging
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

from django.conf import settings

from .models import Context, Entry, Tag, snapshot_entry
from .tjai_utils import fmt_datetime, get_app_tz

logger = logging.getLogger(__name__)

TAG = 'capture'
SOURCE_TAG = 'gmail'
ROOT = Path(settings.BASE_DIR) / 'data' / 'captures'
SITE_URL = 'https://etaverse.com/tjai'
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES = 20
IMAGE_TYPES = {
    'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif',
    'image/webp': '.webp', 'image/bmp': '.bmp', 'image/tiff': '.tif',
    'image/heic': '.heic', 'image/heif': '.heif',
}
_UNSAFE_RE = re.compile(r'[^A-Za-z0-9._-]+')


def _safe_name(index, name, content_type):
    """``NN-<cleaned original stem><ext>``, the extension from the type."""
    stem = _UNSAFE_RE.sub('-', Path(name or '').stem).strip('-.')[:60] or 'image'
    ext = IMAGE_TYPES.get(content_type) or Path(name or '').suffix.lower() or '.img'
    return f'{index:02d}-{stem}{ext}'


def create(subject, sender, gmail_url, note, context_name, tags, files):
    """Store ``files`` (list of (name, content_type, bytes)) and create the
    capture entry. Returns the entry; raises ValueError on an empty,
    over-limit, or non-image upload."""
    if not files:
        raise ValueError('no image files')
    if len(files) > MAX_FILES:
        raise ValueError(f'at most {MAX_FILES} images per capture')
    for name, ctype, data in files:
        if ctype not in IMAGE_TYPES:
            raise ValueError(f'{name}: unsupported type {ctype or "unknown"}')
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f'{name}: larger than {MAX_FILE_BYTES // (1024 * 1024)} MB')

    now = time.time()
    entry_id = str(uuid.uuid7())
    month = datetime.fromtimestamp(now, tz=get_app_tz()).strftime('%Y-%m')
    rel_dir = f'{month}/{entry_id}'
    target = ROOT / rel_dir
    target.mkdir(parents=True, exist_ok=True)
    stored = []
    for i, (name, ctype, data) in enumerate(files, 1):
        fname = _safe_name(i, name, ctype)
        (target / fname).write_bytes(data)
        stored.append({'name': fname, 'size': len(data), 'type': ctype})

    subject = (subject or '').strip() or '(no subject)'
    sender = (sender or '').strip()
    gmail_url = (gmail_url or '').strip()
    note = (note or '').strip()
    head = subject
    if sender:
        head += f' from {sender}'
    if gmail_url:
        head += f' [gmail]({gmail_url})'
    lines = [head]
    if note:
        lines += ['', note]
    lines.append('')
    for f in stored:
        lines.append(f'![{f["name"]}]({SITE_URL}/capture/{entry_id}/{f["name"]})')

    context_obj = Context.objects.filter(name=context_name).first() if context_name else None
    entry = Entry.objects.create(
        id=entry_id,
        content='\n'.join(lines),
        kind='memory',
        context=context_obj,
        data={'capture_dir': rel_dir, 'files': stored, 'subject': subject,
              'sender': sender, 'gmail_url': gmail_url, 'note': note},
        timestamp_created=now,
        timestamp_modified=now,
        is_dirty=1,
    )
    for tag in dict.fromkeys([SOURCE_TAG, TAG] + [t.strip() for t in tags if t and t.strip()]):
        Tag.objects.create(tag_name=tag, entry=entry)
    return entry


def file_path(entry_id, filename):
    """Path of one stored file for a live capture entry, or None."""
    entry = Entry.objects.filter(id=str(entry_id), deleted_at__isnull=True).first()
    if not entry:
        return None
    data = entry.data if isinstance(entry.data, dict) else {}
    rel_dir = data.get('capture_dir')
    if not rel_dir or '/' in filename or filename.startswith('.'):
        return None
    path = ROOT / rel_dir / filename
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def listing(limit=500):
    """Live captures, newest first, for the Captures page."""
    entries = (Entry.objects.filter(tags__tag_name=TAG, kind='memory', deleted_at__isnull=True)
               .select_related('context').distinct().order_by('-timestamp_created')[:limit])
    items = []
    for e in entries:
        data = e.data if isinstance(e.data, dict) else {}
        items.append({
            'id': e.id,
            'datetime': fmt_datetime(e.timestamp_created),
            'subject': data.get('subject') or e.content.split('\n', 1)[0],
            'sender': data.get('sender') or '',
            'gmail_url': data.get('gmail_url') or '',
            'note': data.get('note') or '',
            'context': e.context.name if e.context else '',
            'entry_url': f'{SITE_URL}/entry/?uuid={e.id}',
            'files': [{'name': f['name'], 'size': f.get('size', 0),
                       'url': f'{SITE_URL}/capture/{e.id}/{f["name"]}'}
                      for f in (data.get('files') or [])],
        })
    return items


def delete(entry):
    """Remove the capture's files and move its entry to Trash."""
    data = entry.data if isinstance(entry.data, dict) else {}
    rel_dir = data.get('capture_dir')
    if rel_dir:
        target = ROOT / rel_dir
        try:
            target.resolve().relative_to(ROOT.resolve())
        except ValueError:
            logger.error('capture %s: dir %s outside %s, files left in place', entry.id, rel_dir, ROOT)
        else:
            if target.exists():
                shutil.rmtree(target)
    snapshot_entry(entry, changed_by='capture-delete')
    now = time.time()
    entry.deleted_at = now
    entry.timestamp_modified = now
    entry.is_dirty = 1
    entry.save(update_fields=['deleted_at', 'timestamp_modified', 'is_dirty'])


def delete_file(entry, filename):
    """Remove one image from a capture: the file, its entry in data.files,
    and its image line in the content. Deleting the last image deletes the
    capture. Returns the number of images left."""
    data = entry.data if isinstance(entry.data, dict) else {}
    files = data.get('files') or []
    if not any(f['name'] == filename for f in files):
        raise FileNotFoundError(filename)
    if len(files) == 1:
        delete(entry)
        return 0
    path = file_path(entry.id, filename)
    snapshot_entry(entry, changed_by='capture-delete')
    if path is not None:
        path.unlink()
    data['files'] = [f for f in files if f['name'] != filename]
    marker = f']({SITE_URL}/capture/{entry.id}/{filename})'
    entry.content = '\n'.join(
        line for line in entry.content.split('\n')
        if not (line.startswith('![') and line.endswith(marker)))
    entry.data = data
    entry.timestamp_modified = time.time()
    entry.is_dirty = 1
    entry.save(update_fields=['content', 'data', 'timestamp_modified', 'is_dirty'])
    return len(data['files'])
