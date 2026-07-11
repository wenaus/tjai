#!/usr/bin/env python3
"""Focused compatibility checks for the stdlib-only TJAI client."""

import json
import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch


with tempfile.TemporaryDirectory() as app_dir:
    os.environ["TJAI_APP_DIR"] = app_dir

    from tj.state import _get_agent_status_brief
    from tj.server import get_api_key
    from tj.uuid7 import uuid7
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
    Path(app_dir, "agent_status.json").write_text(json.dumps({"last_error": error}))
    with patch("tj.config.get_location_name", return_value="test-host"):
        status = _get_agent_status_brief()
    assert error in status

    repo_env = Path(app_dir, "repo.env")
    repo_env.write_text('TJAI_API_KEY="repo-test-key"\n')
    get_api_key.cache_clear()
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("tj.server.Path.home", return_value=Path(app_dir, "empty-home")),
        patch("tj.server.REPO_ENV_FILE", repo_env),
    ):
        assert get_api_key() == "repo-test-key"
    get_api_key.cache_clear()

print("client compatibility checks passed")
