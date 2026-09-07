#!/usr/bin/env python3
"""Write a day's dialog as one file per session, with a manifest.

Usage: dialog_streams.py YYYY-MM-DD [--out DIR]

The split, the classification and the sizes come from dialog_prep, the same
preparation the assessor runs; this writes them out to look at. Each stream
file holds one session's turns in the form the assessor reads. Replays are
listed in the manifest and not written.

Output: DIR/<date>/<host>-<session8>.txt and DIR/<date>/manifest.{txt,json};
DIR defaults to data/dialog-streams beside the tjai tree.
"""
import json
import sys
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup

from dialog_prep import CHARS_PER_TOKEN, fetch_dialog, format_turn, split_sessions


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        sys.exit(2)
    date_str = args[0]
    out_root = Path(__file__).resolve().parent.parent / 'data' / 'dialog-streams'
    if '--out' in sys.argv:
        out_root = Path(sys.argv[sys.argv.index('--out') + 1])
    out_dir = out_root / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    turns = fetch_dialog(date_str)
    if not turns:
        print(f"no dialog for {date_str}")
        sys.exit(1)

    rows = []
    for s in split_sessions(turns):
        row = {k: s[k] for k in ('session_id', 'host', 'client', 'kind', 'user_turns', 'assistant_turns',
                                 'first', 'last', 'models', 'chars', 'est_tokens')}
        row['file'] = None
        if s['kind'] != 'replay':
            fname = f"{s['host'] or 'nohost'}-{s['session_id'][:8]}.txt"
            (out_dir / fname).write_text('\n\n'.join(format_turn(t) for t in s['turns']) + '\n', encoding='utf-8')
            row['file'] = fname
        rows.append(row)

    written = [r for r in rows if r['file']]
    manifest = {'date': date_str, 'turns': len(turns), 'sessions': len(rows), 'written': len(written),
                'chars_per_token': CHARS_PER_TOKEN, 'sessions_detail': rows}
    (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    lines = [f"dialog streams for {date_str}: {len(turns)} turns, {len(rows)} sessions, "
             f"{len(written)} written (est tokens = chars / {CHARS_PER_TOKEN})", '']
    hdr = f"{'session':<9} {'host':<12} {'kind':<9} {'user':>5} {'asst':>5} {'first':>8} {'last':>8} {'chars':>9} {'~tokens':>8}  models"
    lines += [hdr, '-' * len(hdr)]
    for r in rows:
        models = ', '.join(f"{m}:{n}" for m, n in sorted(r['models'].items(), key=lambda kv: -kv[1]))
        lines.append(f"{r['session_id'][:8]:<9} {r['host']:<12} {r['kind']:<9} {r['user_turns']:>5} "
                     f"{r['assistant_turns']:>5} {r['first']:>8} {r['last']:>8} {r['chars']:>9,} "
                     f"{r['est_tokens']:>8,}  {models}")
    (out_dir / 'manifest.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))
    print(f"\nwritten to {out_dir}")


if __name__ == '__main__':
    main()
