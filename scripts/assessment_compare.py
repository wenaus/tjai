#!/usr/bin/env python3
"""Compare assessment variants for one date on detection and generosity.

Two assessors can find the same events and still write different endpoints:
detection is how much a reader notices per 100K tokens of input, generosity
is how it scores what it noticed. The endpoint on the dashboard moves with
both, so a switch of assessor is only readable if they are reported apart.

Usage: assessment_compare.py YYYY-MM-DD variant [variant ...]

Reads the saved raw responses each variant wrote (<date>-<variant>-<n>.txt)
and its plan, so it costs nothing and can be re-run after the fact.
"""
import json
import sys
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup

from assessment_gemini import _response_dir, parse_response


def load_variant(date_str, variant):
    """Every part's scores for one variant, from its saved raw responses."""
    d = _response_dir()
    result_path = d / f'{date_str}-{variant}-result.json'
    result = json.loads(result_path.read_text()) if result_path.exists() else {}

    parts, scores = [], []
    for n in range(1, 40):
        raw = d / f'{date_str}-{variant}-{n}.txt'
        if not raw.exists():
            break
        try:
            data, _ = parse_response(raw.read_text(encoding='utf-8'))
            part_scores = data.get('scores', [])
        except Exception as e:
            print(f"  {variant} part {n}: unparseable ({e})", file=sys.stderr)
            part_scores = []
        parts.append(len(part_scores))
        scores.extend(part_scores)
    return result, parts, scores


def summarize(scores):
    """Detection and generosity, kept apart."""
    vals = []
    for s in scores:
        v = s.get('score')
        if isinstance(v, (int, float)):
            vals.append(v)
    pos = [v for v in vals if v > 0]
    neg = [v for v in vals if v < 0]
    return {
        'events': len(scores),
        'scored': len(vals),
        'positive': len(pos),
        'negative': len(neg),
        'zero': len(vals) - len(pos) - len(neg),
        'sum': sum(vals),
        'sum_positive': sum(pos),
        'sum_negative': sum(neg),
        'mean': round(sum(vals) / len(vals), 2) if vals else None,
    }


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    date_str, variants = sys.argv[1], sys.argv[2:]

    rows = []
    for v in variants:
        result, parts, scores = load_variant(date_str, v)
        s = summarize(scores)
        s.update({
            'variant': v,
            'model': result.get('model', '?'),
            'cap': result.get('cap_tokens'),
            'calls': len(parts) or result.get('calls'),
            'tokens': result.get('total_est_tokens'),
            'per_part': parts,
        })
        s['per_100k'] = (round(s['events'] * 100000 / s['tokens'], 1)
                         if s['tokens'] else None)
        rows.append(s)

    print(f"\nAssessment comparison for {date_str}\n")
    head = (f"{'variant':<12}{'model':<26}{'cap':>7}{'calls':>6}{'events':>7}"
            f"{'/100K':>7}{'endpoint':>9}{'mean':>7}{'neg':>5}{'pos':>5}")
    print(head)
    print('-' * len(head))
    for r in rows:
        print(f"{r['variant']:<12}{str(r['model']):<26}"
              f"{str(r['cap'] or '-'):>7}{str(r['calls'] or '-'):>6}"
              f"{r['events']:>7}{str(r['per_100k'] or '-'):>7}"
              f"{r['sum']:>+9}{str(r['mean'] or '-'):>7}"
              f"{r['negative']:>5}{r['positive']:>5}")

    print("\nDetection is /100K, generosity is mean and the endpoint.")
    print("Healthy detection band from docs/assessment.md: 13-27 per 100K; 6-10 is thin.\n")
    for r in rows:
        print(f"{r['variant']}: events per part {r['per_part']}, "
              f"positive sum {r['sum_positive']:+}, negative sum {r['sum_negative']:+}")


if __name__ == '__main__':
    main()
