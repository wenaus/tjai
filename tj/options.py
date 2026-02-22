"""Consolidated option parsing for tj.

This module is imported first and parses all command-line options once.
Other modules import from here to access parsed values.

Usage:
    from tj.options import DEBUG, DB_PATH, get_command_args
"""
import shlex
import sys
import traceback
import time
from pathlib import Path
from typing import Optional

# --- Raw argv processing ---
# The bash function passes: tj.py "$*" which gives us a single string arg
# We need to tokenize before parsing options

_raw_argv = sys.argv.copy()
_processed_argv: list[str] = [_raw_argv[0]] if _raw_argv else []

# Tokenize compound argument from bash function wrapper
if len(_raw_argv) == 2 and ' ' in _raw_argv[1]:
    # Bash wrapper passes "$*" as a single string - tokenize it
    last_arg = _raw_argv[1]
    if last_arg:
        tokens = shlex.split(last_arg)
        _processed_argv.extend(tokens)
elif len(_raw_argv) >= 2:
    # Direct invocation - args are already properly separated
    _processed_argv.extend(_raw_argv[1:])

# --- Option definitions and parsing ---
_START_TIME = time.time()

# Parsed option values
DEBUG: bool = False
DB_PATH: Optional[Path] = None
FILE_PATH: Optional[Path] = None
FILE_CONTENT: Optional[str] = None

# Args remaining after option extraction (for command parsing)
_command_args: list[str] = []

# Parse options from processed argv
i = 1
while i < len(_processed_argv):
    arg = _processed_argv[i]

    if arg == '--debug':
        DEBUG = True
    elif arg.startswith('--db='):
        DB_PATH = Path(arg.split('=', 1)[1])
    elif arg in ['-f', '--file']:
        if i + 1 < len(_processed_argv):
            FILE_PATH = Path(_processed_argv[i + 1])
            i += 1  # Skip next arg
        else:
            print("Error: -f/--file requires a file path", file=sys.stderr)
            sys.exit(1)
    else:
        # Not an option - keep for command parsing
        _command_args.append(arg)
    i += 1

# Load file content if specified
if FILE_PATH:
    try:
        FILE_CONTENT = FILE_PATH.read_text().strip()
    except FileNotFoundError:
        print(f"Error: File not found: {FILE_PATH}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        traceback.print_exc()
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)


def get_command_args() -> list[str]:
    """Get command arguments with options stripped."""
    return _command_args.copy()


def get_original_argv() -> list[str]:
    """Get original sys.argv before any processing."""
    return _raw_argv.copy()


# --- Debug timing infrastructure ---
_TIMINGS: list[tuple[str, float]] = []


def debug_time(label: str, start: float) -> None:
    """Record timing if debug mode enabled."""
    if DEBUG:
        elapsed = (time.time() - start) * 1000
        _TIMINGS.append((label, elapsed))


def debug_mark(label: str) -> None:
    """Record time since program start."""
    if DEBUG:
        elapsed = (time.time() - _START_TIME) * 1000
        _TIMINGS.append((f"@{label}", elapsed))


def print_debug_timings() -> None:
    """Print accumulated timings."""
    if DEBUG and _TIMINGS:
        print("\n--- Debug Timings ---")
        for label, ms in _TIMINGS:
            if label.startswith('@'):
                print(f"  {label}: {ms:.0f}ms (since start)")
            else:
                print(f"  {label}: {ms:.1f}ms")
        total = time.time() - _START_TIME
        print(f"  TOTAL: {total*1000:.0f}ms")


# Register atexit handler for debug output
if DEBUG:
    import atexit
    atexit.register(print_debug_timings)
    debug_mark("options_parsed")
