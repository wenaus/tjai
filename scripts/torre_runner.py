#!/usr/bin/env python3
"""torre-plan action runner — tjai-side shim for the torre-code harness.

Invoked by tjai action_agent as a mechanical_script:

    mechanical_script = "torre_runner.py <staging-dir>"

Responsibilities:
  1. Load ~/.env so ANTHROPIC_API_KEY is available even if supervisord's
     env for action_agent doesn't export it.
  2. Run torre-code's `torre plan` via its own venv, using the inputs
     prepared in the staging dir by `torre capture`.
  3. On success: create a tjai memory entry with the plan markdown,
     link it back to the action entry (relation + data pointer), and
     mark the action entry status=done.
  4. On failure: write the error tail into the action entry's
     data.torre_error and set status=blocked; exit nonzero so
     action_agent's own bookkeeping records run_status=failed.

This shim is intentionally thin — all torre-code logic lives in the
torre-code repo. This file only bridges tjai's action_agent invocation
convention (SCRIPTS_DIR / .py, python subprocess) to torre-code's
venv-based CLI. Changes to torre-code behavior do NOT require editing
this shim or restarting action_agent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import bootstrap  # noqa: F401 - Django setup
from tjai_app.models import Entry, Relation

TORRE_BIN = Path("/home/admin/github/tjrepo/torre-code/bin/torre")
ENV_FILE = Path.home() / ".env"
RESULT_CONTEXT = "torre-code"
PLAN_TIMEOUT_S = 1800    # 30 min; torre plan is one-turn so this is safe


def _die(msg: str, code: int = 1) -> None:
    sys.stderr.write(f"torre_runner: {msg}\n")
    sys.exit(code)


def _load_dotenv(path: Path) -> None:
    """Export KEY=VALUE lines from ~/.env into os.environ (does not overwrite)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _build_argv(staging: Path, meta: dict) -> list[str]:
    out_path = staging / "plan.md"
    argv: list[str] = [
        str(TORRE_BIN), "plan",
        str(staging / "problem.md"),
        "-o", str(out_path),
        "-b", str(meta["budget_usd"]),
        "--transcript-max-chars", str(meta["transcript_max_chars"]),
    ]
    if meta.get("model") == "claude-sonnet-4-6":
        argv.append("--cheap")
    if not meta.get("include_reasoning", True):
        argv.append("--no-reasoning")
    if meta.get("transcript_turns") is not None:
        argv += ["--transcript-turns", str(meta["transcript_turns"])]
    files_list = (staging / "files.list")
    if files_list.is_file():
        for f in files_list.read_text(encoding="utf-8").strip().splitlines():
            if f:
                argv += ["-r", f]
    tp = staging / "transcript.path"
    if tp.is_file():
        transcript = tp.read_text(encoding="utf-8").strip()
        if transcript:
            argv += ["-t", transcript]
    return argv


def _mark_action_failed(action: Entry, err_tail: str) -> None:
    data = action.data or {}
    data["torre_error"] = err_tail[-1000:]
    data["run_status"] = "failed"
    data["run_completed_at"] = int(datetime.now().timestamp())
    action.data = data
    action.status = "blocked"
    action.save(update_fields=["data", "status", "modified"])


def _mark_action_done(action: Entry, result_entry: Entry) -> None:
    data = action.data or {}
    data["run_status"] = "completed"
    data["run_completed_at"] = int(datetime.now().timestamp())
    data["result_entry_uuid"] = str(result_entry.id)
    data["result_entry_id"] = (result_entry.data or {}).get("entry_id")
    action.data = data
    action.status = "done"
    action.save(update_fields=["data", "status", "modified"])


def _create_result_entry(action: Entry, plan_text: str, staging: Path) -> Entry:
    slug = ((action.data or {}).get("entry_id") or "torre-plan").replace(
        "torre-plan-", "torre-plan-result-")
    result = Entry.objects.create(
        content=plan_text,
        kind="memory",
        context=RESULT_CONTEXT,
        tags=["torre-plan-result", "fromai"],
        priority=1,
        data={
            "entry_id": slug,
            "source_action_uuid": str(action.id),
            "source_action_entry_id": (action.data or {}).get("entry_id"),
            "staging_dir": str(staging),
        },
    )
    # Relation so the entry graph links them.
    try:
        Relation.objects.create(
            entry1_id=action.id,
            entry2_id=result.id,
            relation_type="produced",
            data={"by": "torre-plan"},
        )
    except Exception as e:
        # Non-fatal — the data pointer above is the primary link.
        sys.stderr.write(f"torre_runner: relation creation failed: {e!r}\n")
    return result


def main() -> int:
    if len(sys.argv) != 2:
        _die(f"usage: torre_runner.py <staging-dir> (got {sys.argv!r})", 2)
    staging = Path(sys.argv[1]).resolve()
    if not staging.is_dir():
        _die(f"staging dir not found: {staging}", 2)

    meta_path = staging / "meta.json"
    if not meta_path.is_file():
        _die(f"meta.json missing in staging: {meta_path}", 2)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    action_entry_id = meta.get("action_entry_id")
    action_uuid = meta.get("action_uuid")

    action: Entry | None = None
    if action_uuid:
        action = Entry.objects.filter(id=action_uuid).first()
    if action is None and action_entry_id:
        action = Entry.objects.filter(data__entry_id=action_entry_id).first()
    if action is None:
        _die(f"action entry not found: uuid={action_uuid} entry_id={action_entry_id}", 2)

    _load_dotenv(ENV_FILE)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _mark_action_failed(action, "ANTHROPIC_API_KEY not set in env")
        _die("ANTHROPIC_API_KEY missing; marked action blocked", 2)

    argv = _build_argv(staging, meta)
    sys.stdout.write(f"torre_runner: running {' '.join(argv)}\n")
    sys.stdout.flush()

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=PLAN_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as e:
        err = f"torre plan timed out after {PLAN_TIMEOUT_S}s: {e}"
        _mark_action_failed(action, err)
        _die(err, 2)

    # Surface stdout/stderr for action_agent's log capture.
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        _mark_action_failed(action, tail)
        _die(f"torre plan exited {proc.returncode}", proc.returncode)

    plan_file = staging / "plan.md"
    if not plan_file.is_file():
        _mark_action_failed(action, "torre plan exited 0 but plan.md is missing")
        _die("plan.md not produced", 2)
    plan_text = plan_file.read_text(encoding="utf-8")

    result_entry = _create_result_entry(action, plan_text, staging)
    _mark_action_done(action, result_entry)
    sys.stdout.write(
        f"torre_runner: done. result_entry_uuid={result_entry.id} "
        f"entry_id={(result_entry.data or {}).get('entry_id')}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
