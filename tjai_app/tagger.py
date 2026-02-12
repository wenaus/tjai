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


def tag_bookmark(entry) -> list[str]:
    """Apply rule-based tags to a Django ORM bookmark entry.

    Returns list of tag names added.
    """
    from .models import Tag

    added = []
    for tag_name in compute_tags(entry.content):
        _, created = Tag.objects.get_or_create(tag_name=tag_name, entry=entry)
        if created:
            added.append(tag_name)
    return added
