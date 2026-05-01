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

from tjai_app.views import _fix_md_list_spacing, _render_markdown  # noqa: E402


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

    print("markdown render tests passed")


if __name__ == "__main__":
    main()
