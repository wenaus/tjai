#!/usr/bin/env python3
"""Focused compatibility checks for the stdlib-only TJAI client."""

import json
import io
import os
import sqlite3
import tempfile
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


with tempfile.TemporaryDirectory() as app_dir:
    os.environ["TJAI_APP_DIR"] = app_dir

    from tj.state import _get_agent_status_brief
    from tj.server import get_api_key
    from tj.uuid7 import uuid7
    from tj import config as config_module
    from tj.commands.agent import handle_agent
    from tj_agent import sync

    generated = [uuid7() for _ in range(100)]
    assert all(value.version == 7 for value in generated)
    assert all(value.variant == uuid.RFC_4122 for value in generated)
    assert generated == sorted(generated)
    assert len(set(generated)) == len(generated)

    sync.MACHINE_ID_FILE = Path(app_dir, "machine_id")
    machine_id = uuid.UUID(sync.get_machine_id())
    assert machine_id.version == 7

    error = "Server error 401: Unauthorized"
    error_traceback = "Traceback (most recent call last):\n  test failure"
    Path(app_dir, "agent_status.json").write_text(json.dumps({
        "last_error": error,
        "last_error_at": 1.0,
        "last_traceback": error_traceback,
    }))
    with patch("tj.config.get_location_name", return_value="test-host"):
        status = _get_agent_status_brief()
    assert error in status

    command_output = io.StringIO()
    with (
        patch("tj_agent.daemon.is_running", return_value=True),
        patch("tj_agent.daemon.is_daemon_installed", return_value=True),
        redirect_stdout(command_output),
        redirect_stderr(command_output),
    ):
        handle_agent(SimpleNamespace(args=[]))
    assert "Last traceback:" in command_output.getvalue()
    assert error_traceback in command_output.getvalue()

    Path(app_dir, "agent_status.json").write_text("not-json")
    with patch("tj.config.get_location_name", return_value="test-host"):
        status = _get_agent_status_brief()
    assert "JSONDecodeError" in status

    repo_env = Path(app_dir, "repo.env")
    repo_env.write_text('TJAI_API_KEY="repo-test-key"\n')
    get_api_key.cache_clear()

    assert config_module._is_windows_mounted_path(
        Path("/mnt/c/Users/test/Dropbox")
    )
    assert not config_module._is_windows_mounted_path(Path(app_dir))

    source_db = Path(app_dir, "windows-source.db")
    migrated_db = Path(app_dir, "linux-cache", "migrated.db")
    source_conn = sqlite3.connect(source_db)
    source_conn.execute("CREATE TABLE sample (value TEXT)")
    source_conn.execute("INSERT INTO sample VALUES ('preserved')")
    source_conn.commit()
    source_conn.close()
    config_module._migrate_sqlite_database(source_db, migrated_db)
    migrated_conn = sqlite3.connect(migrated_db)
    assert migrated_conn.execute("SELECT value FROM sample").fetchone()[0] == "preserved"
    migrated_conn.close()

    reported_error = {}
    with (
        patch(
            "tj_agent.sync.get_last_sync_time",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ),
        patch(
            "tj_agent.sync.write_status",
            side_effect=lambda **kwargs: reported_error.update(kwargs),
        ),
        patch("tj_agent.sync.logger.error"),
    ):
        try:
            sync.sync_cycle()
        except RuntimeError as exc:
            assert "sync baseline read failed" in str(exc)
        else:
            raise AssertionError("sync_cycle did not surface the database error")
    assert "OperationalError" in reported_error["last_error"]
    assert "disk I/O error" in reported_error["last_traceback"]

    configured_dir = Path(app_dir, "windows-mount")
    configured_dir.mkdir()
    configured_db = configured_dir / "tjai_wsl-test.db"
    configured_conn = sqlite3.connect(configured_db)
    configured_conn.execute("CREATE TABLE local_entry (value TEXT)")
    configured_conn.execute("INSERT INTO local_entry VALUES ('kept')")
    configured_conn.commit()
    configured_conn.close()
    config_module._cached_config = {
        "db_dir": str(configured_dir),
        "location_name": "wsl-test",
    }
    with (
        patch("tj.config._running_under_wsl", return_value=True),
        patch("tj.config._is_windows_mounted_path", return_value=True),
    ):
        effective_db = config_module.get_db_path()
    assert effective_db == Path(app_dir, "db", "tjai_wsl-test.db")
    effective_conn = sqlite3.connect(effective_db)
    assert effective_conn.execute(
        "SELECT value FROM local_entry"
    ).fetchone()[0] == "kept"
    effective_conn.close()
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("tj.server.Path.home", return_value=Path(app_dir, "empty-home")),
        patch("tj.server.REPO_ENV_FILE", repo_env),
    ):
        assert get_api_key() == "repo-test-key"
    get_api_key.cache_clear()

print("client compatibility checks passed")
