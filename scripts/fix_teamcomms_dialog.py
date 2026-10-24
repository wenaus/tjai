#!/usr/bin/env python3
"""Correct TeamComms envelopes recorded as operator dialog; preview by default."""

import argparse
import json

import bootstrap  # noqa: F401

from django.db import transaction

from tjai_app.comms_dialog import external_peer_data
from tjai_app.dialog_context import DIALOG_TAG
from tjai_app.models import Entry, Tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    ids = Tag.objects.filter(tag_name=DIALOG_TAG).values_list("entry_id", flat=True)
    candidates = Entry.objects.filter(id__in=ids, deleted_at__isnull=True, data__role="user",
        content__regex=r"^(Another Claude session sent a message:\s*)?\[TeamComms peer ")
    changed = 0
    with transaction.atomic():
        for entry in candidates.select_for_update().iterator():
            peer = external_peer_data(entry.content)
            if peer is None:
                raise ValueError(f"Malformed TeamComms envelope: {entry.id}")
            if args.apply:
                entry.data = {**(entry.data or {}), **peer, "recorded_role": "user"}
                entry.save(update_fields=["data"])
            changed += 1
    print(json.dumps({"mode": "applied" if args.apply else "preview", "turns": changed}))


if __name__ == "__main__":
    main()
