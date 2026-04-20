"""Abort a worker's in-flight claim and transmit the failure to the server.

The remote-worker protocol has no "cancel" message — the only terminal
signal the server understands is a POST to /api/worker/result with
status='failed'. When a worker is killed (SIGTERM from launchctl bootout,
SIGKILL, crash, power loss) without posting that failure, the server
keeps showing the claim as busy until WORKER_CLAIM_STALE_SECONDS (2h)
elapses — a long, avoidable lag between reality and the UI.

This module closes that gap. `_process_work_async` writes a claim
marker to ~/.tjai/current_claim.json when it starts work and removes
it on any completion path. `abort_current_claim()` reads the marker,
POSTs status='failed' for the entry, and clears the marker.

Two callers:

1. The `tj_agent abort` CLI subcommand — standalone, works even when
   tj_agent itself is already dead. Used by scripts/kill_worker.sh
   before launchctl bootout, so the server is notified *before* the
   worker process disappears.

2. The worker startup path (_run_worker_forever_async) — if a marker
   exists at startup, a previous process crashed/was killed without
   cleanup. We post the failure and clear so the next poll doesn't
   race with a stale claim.

This is "programmatically transmit status when you kill something" —
the kill operation, not an operator curl, is responsible for making
the server state match reality.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from tj_agent import client
from tj_agent.sync import get_machine_id

logger = logging.getLogger(__name__)

CURRENT_CLAIM_FILE = Path(os.path.expanduser("~/.tjai/current_claim.json"))


STATE_PENDING = "pending"  # POST to /api/worker/result not yet sent
STATE_POSTED = "posted"    # POST succeeded; marker just needs unlinking


def _atomic_write(payload: dict) -> None:
    CURRENT_CLAIM_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CURRENT_CLAIM_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(CURRENT_CLAIM_FILE)


def write_current_claim(entry_id: str, capability: str,
                        base_entry_id: str | None,
                        claimed_at: float,
                        ollama_model: str | None = None) -> None:
    """Record the in-flight claim so abort_current_claim can find it.

    Called from _process_work_async right after the worker accepts a
    work item. State is `pending` — POST has not yet been sent.
    Idempotent — last write wins if called repeatedly.
    """
    _atomic_write({
        "state": STATE_PENDING,
        "entry_id": entry_id,
        "capability": capability,
        "base_entry_id": base_entry_id,
        "claimed_at": claimed_at,
        "ollama_model": ollama_model,
    })


def mark_current_claim_posted() -> None:
    """Flip the marker to state=`posted` after worker_result POSTed OK.

    Must be called AFTER a successful client.worker_result call and
    BEFORE clear_current_claim(). Exists to close a narrow race: if the
    POST succeeded but the subsequent unlink fails (filesystem,
    permissions), a bare pending marker left behind would cause the
    next tj_agent startup to re-POST status='failed' for the same
    entry — clobbering the just-succeeded 'done' result, because
    worker_result accepts results unconditionally (even from non-claim-
    holders, per the doc). Flipping to `posted` first makes the startup
    recovery path recognize "POST was already sent, nothing to do" and
    skip the re-abort. Safe no-op if no marker exists.
    """
    claim = read_current_claim()
    if not claim:
        return
    claim["state"] = STATE_POSTED
    try:
        _atomic_write(claim)
    except Exception as e:
        logger.warning("failed to flip current_claim marker to posted: %s", e)


def clear_current_claim() -> None:
    """Remove the claim marker. Called on every completion path AFTER
    the POST is made (and, in the success path, after
    mark_current_claim_posted).

    Safe to call when no marker exists.
    """
    try:
        CURRENT_CLAIM_FILE.unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("failed to clear current_claim marker: %s", e)


def read_current_claim() -> dict | None:
    """Return the in-flight claim marker, or None if absent/unreadable."""
    if not CURRENT_CLAIM_FILE.exists():
        return None
    try:
        return json.loads(CURRENT_CLAIM_FILE.read_text())
    except Exception as e:
        logger.warning("current_claim marker unreadable: %s", e)
        return None


def abort_current_claim(reason: str) -> dict:
    """POST status='failed' for the locally-recorded in-flight claim.

    Returns a result dict:
      {"aborted": True,  "entry_id": "...", "capability": "..."}  on success
      {"aborted": False, "detail": "no claim"}                     no-op
      {"aborted": False, "detail": "<error>", "entry_id": "..."}   POST failed

    The marker is removed on success AND on POST failure where the
    server responded — stale marker after a failed POST would re-abort
    on next startup, which is wrong. If the POST raises (network error),
    the marker is preserved so the next tj_agent startup retries.
    """
    claim = read_current_claim()
    if not claim:
        return {"aborted": False, "detail": "no claim"}

    entry_id = claim.get("entry_id")
    capability = claim.get("capability")
    if not entry_id:
        clear_current_claim()
        return {"aborted": False, "detail": "marker had no entry_id"}

    # If the marker says the result was already posted, the previous
    # process got as far as a successful worker_result POST and only
    # failed to unlink the marker. Re-POSTing would clobber the
    # already-accepted result (worker_result accepts any POST from any
    # machine_id with only a warning). Just clear and return no-op.
    if claim.get("state") == STATE_POSTED:
        clear_current_claim()
        return {"aborted": False,
                "detail": "marker was already posted; cleared",
                "entry_id": entry_id, "capability": capability}

    machine_id = get_machine_id()
    logger.info("abort: POSTing failed for entry=%s cap=%s reason=%s",
                entry_id, capability, reason)
    try:
        client.worker_result(
            machine_id=machine_id,
            entry_id=entry_id,
            status="failed",
            error=f"aborted: {reason}",
            duration_sec=0,
        )
    except Exception as e:
        # Network / server error — keep the marker so the next worker
        # startup will retry the abort.
        logger.warning("abort: POST failed, marker preserved: %s", e)
        return {"aborted": False, "detail": str(e), "entry_id": entry_id,
                "capability": capability}

    clear_current_claim()
    return {"aborted": True, "entry_id": entry_id, "capability": capability}
