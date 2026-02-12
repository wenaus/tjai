"""Bulk import module for tjai.

Provides parsers for various bookmark sources and a generic loader
that creates entries via the tjai services layer.

Standard import format (list of dicts):
    {
        "content": "[Title](url)",    # Markdown link format
        "tags": ["tag1", "tag2"],     # Optional explicit tags from source
        "timestamp": 1609459200.0,    # Unix timestamp for created date
        "context": "places"           # Optional context name
    }

Usage:
    from bulk_import.parsers.dynalist import parse_dynalist
    from bulk_import.loader import bulk_import_bookmarks

    bookmarks = parse_dynalist('/path/to/Links.txt', '/path/to/ToDo.txt')
    results = bulk_import_bookmarks(bookmarks, source_tag='dynalist')
"""

from .loader import bulk_import_bookmarks

__all__ = ['bulk_import_bookmarks']
