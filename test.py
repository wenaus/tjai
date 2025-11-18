#!/usr/bin/env python3
"""Test script for tj dump/restore round-trip."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent
TEST_DB = REPO_ROOT / "test.db"
SAMPLE_DUMP = REPO_ROOT / "sample_dump.sh"
TJ_SCRIPT = REPO_ROOT / "tj.py"


def main():
    # Delete test.db
    if TEST_DB.exists():
        TEST_DB.unlink()
        print(f"Deleted {TEST_DB}")

    # Run sample_dump.sh with tj alias
    print(f"\nLoading {SAMPLE_DUMP}...")
    result = subprocess.run(
        ['bash', '-c', f'shopt -s expand_aliases && alias tj="{TJ_SCRIPT} --test" && source {SAMPLE_DUMP}'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"FAILED loading: {result.stderr}")
        sys.exit(1)

    # Dump the database
    print("Dumping database...")
    result = subprocess.run(
        [str(TJ_SCRIPT), '--test', 'dump'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"FAILED dump: {result.stderr}")
        sys.exit(1)

    dump_output = result.stdout

    # Read original
    with open(SAMPLE_DUMP, 'r') as f:
        original = f.read()

    # Compare exactly
    if original == dump_output:
        print("✓ PASSED - Exact match")
    else:
        print("FAILED - Files differ")
        print("\n=== ORIGINAL ===")
        print(original)
        print("\n=== DUMP ===")
        print(dump_output)
        sys.exit(1)


if __name__ == "__main__":
    main()
