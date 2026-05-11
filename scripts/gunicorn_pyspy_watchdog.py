#!/usr/bin/env python3
"""Capture py-spy dumps when tjai gunicorn workers stay CPU-hot."""

from __future__ import annotations

import argparse
import os
import pwd
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_GUNICORN_MATCH = "/var/www/tjai/.venv/bin/gunicorn"
DEFAULT_PY_SPY = "/var/www/tjai/.venv/bin/py-spy"
DEFAULT_OUTPUT_DIR = "/var/log/tjai/pyspy"


def log(message: str) -> None:
    print(
        f"{datetime.now(UTC).isoformat(timespec='seconds')} {message}",
        flush=True,
    )


def read_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()


def read_uid(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def read_proc_cpu_seconds(pid: int) -> float | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None

    try:
        fields = stat.rsplit(") ", 1)[1].split()
        utime = int(fields[11])
        stime = int(fields[12])
    except (IndexError, ValueError):
        return None

    return (utime + stime) / os.sysconf(os.sysconf_names["SC_CLK_TCK"])


def find_gunicorn_pids(command_match: str, user: str | None) -> dict[int, str]:
    expected_uid = None
    if user:
        try:
            expected_uid = pwd.getpwnam(user).pw_uid
        except KeyError:
            log(f"configured user {user!r} does not exist")
            return {}

    pids: dict[int, str] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        cmdline = read_cmdline(pid)
        if command_match not in cmdline:
            continue
        if expected_uid is not None and read_uid(pid) != expected_uid:
            continue
        pids[pid] = cmdline
    return pids


def dump_stack(
    *,
    py_spy: str,
    pid: int,
    cpu_percent: float,
    cmdline: str,
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"{timestamp}-gunicorn-pid{pid}-cpu{cpu_percent:.0f}.txt"

    command = [py_spy, "dump", "--pid", str(pid), "--native"]
    started = datetime.now(UTC).isoformat(timespec="seconds")
    with output_path.open("w", encoding="utf-8") as out:
        out.write(f"timestamp: {started}\n")
        out.write(f"pid: {pid}\n")
        out.write(f"cpu_percent: {cpu_percent:.1f}\n")
        out.write(f"cmdline: {cmdline}\n")
        out.write(f"command: {' '.join(command)}\n\n")
        result = subprocess.run(
            command,
            stdout=out,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            check=False,
        )
        out.write(f"\nexit_code: {result.returncode}\n")

    return output_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=80.0)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--cooldown", type=float, default=600.0)
    parser.add_argument("--command-match", default=DEFAULT_GUNICORN_MATCH)
    parser.add_argument("--user", default="www-data")
    parser.add_argument("--py-spy", default=DEFAULT_PY_SPY)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    py_spy = Path(args.py_spy)
    if not py_spy.exists():
        log(f"py-spy not found at {py_spy}")
        return 2

    previous: dict[int, tuple[float, float]] = {}
    hot_since: dict[int, float] = {}
    last_dump: dict[int, float] = {}

    log(
        "watching gunicorn workers "
        f"threshold={args.threshold}% duration={args.duration}s interval={args.interval}s"
    )
    while True:
        now = time.monotonic()
        pids = find_gunicorn_pids(args.command_match, args.user)
        seen = set(pids)

        for pid, cmdline in pids.items():
            proc_seconds = read_proc_cpu_seconds(pid)
            if proc_seconds is None:
                continue

            previous_sample = previous.get(pid)
            previous[pid] = (now, proc_seconds)
            if previous_sample is None:
                continue

            prev_time, prev_proc_seconds = previous_sample
            elapsed = max(now - prev_time, 0.001)
            cpu_percent = max(0.0, (proc_seconds - prev_proc_seconds) / elapsed * 100.0)

            if cpu_percent < args.threshold:
                hot_since.pop(pid, None)
                continue

            hot_since.setdefault(pid, now)
            hot_for = now - hot_since[pid]
            if hot_for < args.duration:
                continue

            if now - last_dump.get(pid, 0.0) < args.cooldown:
                continue

            try:
                output_path = dump_stack(
                    py_spy=str(py_spy),
                    pid=pid,
                    cpu_percent=cpu_percent,
                    cmdline=cmdline,
                    output_dir=Path(args.output_dir),
                )
            except Exception as exc:  # noqa: BLE001 - watchdog must keep running
                log(f"py-spy dump failed for pid={pid}: {exc}")
            else:
                last_dump[pid] = now
                log(f"captured {output_path} pid={pid} cpu={cpu_percent:.1f}%")

        for stale_pid in set(previous) - seen:
            previous.pop(stale_pid, None)
            hot_since.pop(stale_pid, None)
            last_dump.pop(stale_pid, None)

        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
