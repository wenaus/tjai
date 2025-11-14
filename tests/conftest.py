import pytest
import tempfile
import os
from pathlib import Path

@pytest.fixture
def isolated_env(monkeypatch):
    """
    A pytest fixture to create a temporary, isolated environment for each test.

    This fixture creates a temporary directory and sets the TJAI_APP_DIR
    environment variable to point to it. This ensures that each test runs
    with a clean, empty database and state, preventing tests from interfering
    with each other or with the user's actual data.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir)
        # Use monkeypatch to set the environment variable for the duration of the test
        monkeypatch.setenv("TJAI_APP_DIR", str(temp_path))
        yield temp_path
