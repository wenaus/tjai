"""Bulk bookmark loader for tjai.

Imports bookmarks using Django ORM with:
- Custom historical timestamps
- URL-based deduplication (not time-based)
- Auto-tagging via tagger module
- Progress reporting

This module is imported by Django views - do not call django.setup() here.
"""

import re
import time
import uuid


def _get_models():
    """Lazy import Django models (only works when called from Django context)."""
    from tjai_app.models import Entry, Tag, Context
    from tjai_app.tagger import tag_bookmark
    return Entry, Tag, Context, tag_bookmark


def extract_url_from_content(content):
    """Extract URL from markdown link content."""
    match = re.search(r'\]\((https?://[^)]+)\)', content)
    if match:
        return match.group(1)
    if content.startswith('http'):
        return content.split()[0]
    return None


def get_existing_urls():
    """Get set of URLs already in database (stripped of query params)."""
    Entry, Tag, Context, tag_bookmark = _get_models()
    existing = set()
    for entry in Entry.objects.filter(kind='bookmark', deleted_at__isnull=True).only('content'):
        url = extract_url_from_content(entry.content)
        if url:
            base_url = url.split('?')[0]
            existing.add(base_url)
    return existing


def bulk_import_bookmarks(items, source_tag=None, skip_existing=True,
                          create_context=False, progress_callback=None,
                          dry_run=False):
    """Import bookmarks from standardized format.

    Args:
        items: List of dicts with:
            - content: Markdown link "[Title](url)" (required)
            - tags: List of tag names (optional)
            - timestamp: Unix timestamp for created date (optional, defaults to now)
            - context: Context name (optional)
        source_tag: Tag to add to all imported entries (e.g., 'dynalist')
        skip_existing: Skip URLs already in database (default True)
        create_context: Auto-create missing contexts (default False)
        progress_callback: Optional function(current, total, message) for progress
        dry_run: If True, validate but don't create entries

    Returns:
        dict with:
            - imported: Number of entries created
            - skipped: Number of duplicates skipped
            - errors: List of error messages
            - auto_tags: Dict of auto-tag name -> count
    """
    Entry, Tag, Context, tag_bookmark = _get_models()

    if progress_callback is None:
        def progress_callback(current, total, msg):
            pass

    results = {
        'imported': 0,
        'skipped': 0,
        'errors': [],
        'auto_tags': {},
    }

    # Get existing URLs for deduplication
    if skip_existing:
        existing_urls = set()
        for entry in Entry.objects.filter(kind='bookmark', deleted_at__isnull=True).only('content'):
            url = extract_url_from_content(entry.content)
            if url:
                existing_urls.add(url.split('?')[0])
    else:
        existing_urls = set()
    progress_callback(0, len(items), f"Found {len(existing_urls)} existing bookmark URLs")

    # Cache contexts
    context_cache = {}
    for ctx in Context.objects.all():
        context_cache[ctx.name] = ctx

    total = len(items)
    for i, item in enumerate(items):
        if i % 100 == 0:
            progress_callback(i, total, f"Processing {i}/{total}...")

        content = item.get('content', '').strip()
        if not content:
            results['errors'].append(f"Item {i}: empty content")
            continue

        # Check for duplicate URL
        url = extract_url_from_content(content)
        if url and skip_existing:
            base_url = url.split('?')[0]
            if base_url in existing_urls:
                results['skipped'] += 1
                continue
            # Add to set to catch duplicates within the import
            existing_urls.add(base_url)

        # Get or create context
        context_obj = None
        context_name = item.get('context')
        if context_name:
            if context_name in context_cache:
                context_obj = context_cache[context_name]
            elif create_context:
                now = time.time()
                context_obj = Context.objects.create(
                    name=context_name,
                    timestamp_created=now,
                    timestamp_modified=now,
                )
                context_cache[context_name] = context_obj
            else:
                results['errors'].append(f"Item {i}: context '{context_name}' does not exist")
                continue

        if dry_run:
            results['imported'] += 1
            continue

        # Create entry with custom timestamp
        timestamp = item.get('timestamp') or time.time()
        entry = Entry.objects.create(
            id=str(uuid.uuid4()),
            content=content,
            kind='bookmark',
            context=context_obj,
            timestamp_created=timestamp,
            timestamp_modified=timestamp,
            is_dirty=1,
        )

        # Apply explicit tags from source
        tags = item.get('tags', [])
        for tag_name in tags:
            if tag_name and tag_name.strip():
                Tag.objects.get_or_create(tag_name=tag_name.strip(), entry=entry)

        # Apply source tag
        if source_tag:
            Tag.objects.get_or_create(tag_name=source_tag, entry=entry)

        # Apply auto-tags via tagger
        auto_tags = tag_bookmark(entry)
        for tag_name in auto_tags:
            results['auto_tags'][tag_name] = results['auto_tags'].get(tag_name, 0) + 1

        results['imported'] += 1

    progress_callback(total, total, "Import complete")
    return results


def print_progress(current, total, message):
    """Simple progress printer for CLI use."""
    if total > 0:
        pct = (current / total) * 100
        print(f"\r[{pct:5.1f}%] {message}", end='', flush=True)
        if current == total:
            print()  # Newline at end
    else:
        print(message)
