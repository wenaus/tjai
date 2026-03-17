"""Rule-based auto-tagger for bookmark entries.

compute_tags() is a pure function (no DB) usable from any backend.
tag_bookmark() is a Django ORM wrapper for server-side paths.
"""

import re

URL_RULES = [
    (re.compile(r'youtube\.com/|youtu\.be/'), 'video'),
    (re.compile(r'github\.com/'), 'github'),
    (re.compile(r'arxiv\.org/'), 'paper'),
]

TITLE_RULES = [
    (re.compile(r'recipe', re.IGNORECASE), 'recipe'),
]


def extract_url(content: str) -> str:
    """Extract URL from bookmark content (markdown or bare)."""
    match = re.search(r'\]\((https?://[^)]+)\)', content)
    if match:
        return match.group(1)
    if content.startswith('http'):
        return content.split()[0]
    return ''


def extract_title(content: str) -> str:
    """Extract title from markdown link content."""
    match = re.match(r'\[([^\]]*)\]', content)
    return match.group(1) if match else ''


def compute_tags(content: str) -> list[str]:
    """Return auto-tag names for bookmark content. Pure function, no DB."""
    url = extract_url(content)
    title = extract_title(content)
    tags = []

    for pattern, tag_name in URL_RULES:
        if pattern.search(url):
            tags.append(tag_name)

    for pattern, tag_name in TITLE_RULES:
        if pattern.search(title):
            tags.append(tag_name)

    return tags


def extract_inline_metadata(content: str) -> tuple[str, list[str], str | None]:
    """Extract :tags and =context from content text, return cleaned content.

    Same convention as the CLI parser in tj/commands/create.py.
    Tags must start with alpha character. Context is first =word found
    (outside of markdown links).

    Returns (cleaned_content, tag_names, context_or_none).
    """
    tags = []
    context = None

    # Extract =context (first occurrence not inside a markdown link)
    # Match =word that isn't preceded by ( which would be part of ](url)
    ctx_match = re.search(r'(?<!\()(?<!\])\s=([a-zA-Z][a-zA-Z0-9_-]*)\b', content)
    if ctx_match:
        context = ctx_match.group(1)
        content = content[:ctx_match.start()] + content[ctx_match.end():]

    # Extract :tags — words starting with : where tag starts with alpha
    for match in re.finditer(r'(?<!\S):([a-zA-Z][a-zA-Z0-9_-]*)\b', content):
        tags.append(match.group(1))

    # Strip :tags from content
    content = re.sub(r'(?<!\S):[a-zA-Z][a-zA-Z0-9_-]*\b', '', content)
    content = re.sub(r'  +', ' ', content).strip()

    return content, tags, context


def tag_bookmark(entry) -> list[str]:
    """Apply rule-based tags and extract inline :tags/=context from content.

    Returns list of tag names added.
    """
    from .models import Tag, Context

    added = []

    # Rule-based tags from URL/title patterns
    for tag_name in compute_tags(entry.content):
        _, created = Tag.objects.get_or_create(tag_name=tag_name, entry=entry)
        if created:
            added.append(tag_name)

    # Extract inline :tags and =context from content
    cleaned, inline_tags, context_name = extract_inline_metadata(entry.content)

    for tag_name in inline_tags:
        _, created = Tag.objects.get_or_create(tag_name=tag_name, entry=entry)
        if created:
            added.append(tag_name)

    # Apply context if found and valid
    if context_name:
        ctx = Context.objects.filter(name=context_name).first()
        if ctx:
            entry.context = ctx

    # Update content if it changed (tags/context stripped)
    if cleaned != entry.content:
        entry.content = cleaned
        entry.save(update_fields=['content', 'context_id'])
    elif context_name:
        entry.save(update_fields=['context_id'])

    return added
