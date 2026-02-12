"""Parsers for various bookmark sources.

Each parser takes source-specific input and returns a list of dicts
in the standard import format.
"""

from .dynalist import parse_dynalist

__all__ = ['parse_dynalist']
