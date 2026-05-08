#!/usr/bin/env python3
"""Regression checks for tjai Markdown rendering helpers."""

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tjai_project.settings.base")
os.environ.setdefault("DJANGO_DATABASE_URL", "postgresql://tjai:tjai@localhost:5432/tjai")

import django  # noqa: E402

django.setup()

from tjai_app.views import (  # noqa: E402
    _fix_md_list_spacing,
    _linkify_rendered_html,
    _render_markdown,
)


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_contains(text, needle, label):
    if needle not in text:
        raise AssertionError(f"{label}: expected {needle!r} in {text!r}")


def assert_not_contains(text, needle, label):
    if needle in text:
        raise AssertionError(f"{label}: did not expect {needle!r} in {text!r}")


def main():
    html = _render_markdown(
        "Steps in physicist-to-prod-task workflow implementation\n"
        "  - [Sakib thread](https://mail.google.com/mail/u/0/#inbox/FMfcgzQgLXtGkgCfQRvnlJcMbhnbLJGd)",
        extensions=["tables", "fenced_code"],
    )
    assert_contains(html, '<a href="https://mail.google.com/mail/u/0/#inbox/FMfcgzQgLXtGkgCfQRvnlJcMbhnbLJGd">Sakib thread</a>', "indented list link")
    assert_not_contains(html, "<pre><code>", "indented list should not become code block")

    source = (
        "They have a lot of documents.\n"
        "  - [google form to track PWG requests for new datasets](https://docs.google.com/forms/d/12sAZTWfw8F-Ze9Ln2CbYjy_sYB6RKt3tsZ0zTxZLH9M/edit)\n"
        "  - [overview tracking document](https://docs.google.com/spreadsheets/d/1BJeq3AYwefNC9m3palH6T0SHMxmRmHpOzLTSa_6SZIU/edit?usp=sharing)"
    )
    normalized = _fix_md_list_spacing(source)
    assert_equal(
        normalized,
        "They have a lot of documents.\n\n"
        "- [google form to track PWG requests for new datasets](https://docs.google.com/forms/d/12sAZTWfw8F-Ze9Ln2CbYjy_sYB6RKt3tsZ0zTxZLH9M/edit)\n"
        "- [overview tracking document](https://docs.google.com/spreadsheets/d/1BJeq3AYwefNC9m3palH6T0SHMxmRmHpOzLTSa_6SZIU/edit?usp=sharing)",
        "same-indented list block normalization",
    )
    html = _render_markdown(source, extensions=["tables", "fenced_code"])
    assert_contains(html, '<li><a href="https://docs.google.com/forms/d/12sAZTWfw8F-Ze9Ln2CbYjy_sYB6RKt3tsZ0zTxZLH9M/edit">google form to track PWG requests for new datasets</a></li>', "first sibling list item")
    assert_contains(html, '<li><a href="https://docs.google.com/spreadsheets/d/1BJeq3AYwefNC9m3palH6T0SHMxmRmHpOzLTSa_6SZIU/edit?usp=sharing">overview tracking document</a></li>', "second sibling list item")
    assert_not_contains(html, "new datasets</a><ul>", "second same-indent item should not nest under first")

    source = (
        "Documents\n"
        "  - first\n"
        "  - second\n"
        "    - second child"
    )
    normalized = _fix_md_list_spacing(source)
    assert_equal(
        normalized,
        "Documents\n\n- first\n- second\n  - second child",
        "relative nested indentation survives block normalization",
    )
    html = _render_markdown(source, extensions=["tables", "fenced_code"])
    assert_contains(html, "<li>first</li>", "first sibling before nested item")
    assert_contains(html, "<li>second<ul>", "second item has nested child")
    assert_contains(html, "<li>second child</li>", "nested child survives")

    html = _render_markdown(
        "Intro paragraph\n"
        "- [Example](https://example.com)",
        extensions=["tables", "fenced_code"],
    )
    assert_contains(html, "<p>Intro paragraph</p>", "paragraph before list remains paragraph")
    assert_contains(html, '<li><a href="https://example.com">Example</a></li>', "top-level list link")

    html = _render_markdown(
        "- parent item\n"
        "  - child item",
        extensions=["tables", "fenced_code"],
    )
    assert_contains(html, "<li>child item</li>", "nested child list survives")

    html = _render_markdown(
        "- parent item\n"
        "wrapped continuation",
        extensions=["tables", "fenced_code"],
    )
    assert_contains(html, "<li>parent item wrapped continuation</li>", "wrapped list continuation still rejoins")

    html = _render_markdown(
        "- parent item with **bold text**",
        extensions=["tables", "fenced_code"],
    )
    assert_contains(html, "<strong>bold text</strong>", "bold text inside list item renders")

    html = _linkify_rendered_html(_render_markdown(
        "Links\n"
        "- https://github.com/eic/corun-mcp-server\n"
        "- already [linked](https://example.com/path?x=1&y=2)\n"
        "- sentence https://example.org/test.",
        extensions=["tables", "fenced_code"],
    ))
    assert_contains(
        html,
        '<a target="_blank" href="https://github.com/eic/corun-mcp-server">'
        'https://github.com/eic/corun-mcp-server</a>',
        "bare URL in list is linkified",
    )
    assert_contains(
        html,
        '<a href="https://example.com/path?x=1&amp;y=2">linked</a>',
        "existing markdown link is preserved",
    )
    assert_contains(
        html,
        '<a target="_blank" href="https://example.org/test">'
        'https://example.org/test</a>.',
        "trailing sentence punctuation stays outside link",
    )

    html = _linkify_rendered_html(_render_markdown(
        "```text\nhttps://example.com/code\n```",
        extensions=["tables", "fenced_code"],
    ))
    assert_not_contains(
        html,
        '<a target="_blank" href="https://example.com/code">',
        "bare URL in fenced code is not linkified",
    )

    print("markdown render tests passed")


if __name__ == "__main__":
    main()
