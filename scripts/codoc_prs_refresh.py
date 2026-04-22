#!/usr/bin/env python3
"""Thin wrapper invoked by the tjai action agent — runs the corun-ai
PR cache refresher out-of-process, because the cache lives in
corun-ai's Django settings world, not tjai's.

Usage (via tjai action `mechanical_script`):
    codoc_prs_refresh.py --delta   (2-hour cron)
    codoc_prs_refresh.py --full    (nightly)

Exits non-zero on failure so the action-agent surfaces the error.
"""
import argparse
import subprocess
import sys

CORUN_PY = '/var/www/corun-ai/.venv/bin/python3'
CORUN_SCRIPT = '/var/www/corun-ai/scripts/refresh_prs_cache.py'


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--delta', action='store_true')
    g.add_argument('--full', action='store_true')
    args = ap.parse_args()

    mode_arg = '--delta' if args.delta else '--full'
    # Clear tjai's DJANGO_SETTINGS_MODULE so it doesn't leak into the corun
    # subprocess and override the one it sets for itself.
    import os
    env = {k: v for k, v in os.environ.items() if k != 'DJANGO_SETTINGS_MODULE'}
    try:
        proc = subprocess.run(
            [CORUN_PY, CORUN_SCRIPT, mode_arg],
            capture_output=True, text=True, timeout=600, env=env,
        )
    except subprocess.TimeoutExpired:
        print(f'codoc_prs_refresh: timeout after 600s running {CORUN_SCRIPT} {mode_arg}',
              file=sys.stderr)
        return 2

    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)
    return proc.returncode


if __name__ == '__main__':
    sys.exit(main())
