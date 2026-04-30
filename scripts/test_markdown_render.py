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

from tjai_app.views import _render_markdown  # noqa: E402


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

    print("markdown render tests passed")


if __name__ == "__main__":
    main()
