"""Rule-based auto-tagger for bookmark entries."""

import re
from .models import Tag

# (compiled_regex, tag_name, field)
# field: 'url' matches against the URL, 'title' matches against the title
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


def tag_bookmark(entry) -> list[str]:
    """Apply rule-based tags to a bookmark entry.

    Returns list of tag names added.
    """
    url = extract_url(entry.content)
    title = extract_title(entry.content)
    added = []

    for pattern, tag_name in URL_RULES:
        if pattern.search(url):
            _, created = Tag.objects.get_or_create(tag_name=tag_name, entry=entry)
            if created:
                added.append(tag_name)

    for pattern, tag_name in TITLE_RULES:
        if pattern.search(title):
            _, created = Tag.objects.get_or_create(tag_name=tag_name, entry=entry)
            if created:
                added.append(tag_name)

    return added
