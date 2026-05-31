#!/usr/bin/env bash
# Single source of truth for creating/refreshing a tjai virtualenv.
#
#   make_venv.sh [VENV_DIR] [TARGET]
#     VENV_DIR  path to the venv        (default: <repo>/.venv)
#     TARGET    requirements set        (default: dev; deploy passes prod)
#
#   Interpreter version  ->  .python-version      (uv fetches it if absent)
#   Dependency set       ->  requirements/<TARGET>.txt
#
# This is the ONLY code path that builds a tjai venv. Both local dev setup and
# deploy/update_from_dev.sh call it, so dev and prod cannot drift. If an
# existing venv's interpreter no longer satisfies .python-version, it is rebuilt
# -- the requested version is authoritative, and drift self-heals not silently.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
VENV="${1:-$REPO_ROOT/.venv}"
TARGET="${2:-dev}"
REQ="$REPO_ROOT/requirements/${TARGET}.txt"
PYVER=$(tr -d '[:space:]' < "$REPO_ROOT/.python-version")

# Install uv-managed interpreters into a SHARED, world-readable location rather
# than the running user's home. Deploy runs as root; if the interpreter lived in
# /root/.local (mode 700) the prod venv's python would be unreachable by the
# www-data gunicorn worker and the site would fail to start. /opt/uv/python is
# readable by every service user and is the same dir whether dev (admin) or
# deploy (root) builds it, so the interpreter is shared, not duplicated.
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/opt/uv/python}"

command -v uv >/dev/null 2>&1 || {
  echo "ERROR: uv not found on PATH."
  echo "Install: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
}
[[ -f "$REQ" ]] || { echo "ERROR: requirements file not found: $REQ"; exit 1; }

py_version() { "$1/bin/python" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo none; }

# Rebuild when the venv's interpreter no longer satisfies the request.
# .python-version is a floor-style request (e.g. "3.14" = newest 3.14.x), so a
# venv on 3.14.3 satisfies "3.14" and must NOT be rebuilt -- match on the
# series, not an exact string, or every run would needlessly rebuild.
if [[ -d "$VENV" ]]; then
  current=$(py_version "$VENV")
  case "$current" in
    "$PYVER"|"$PYVER".*) ;;  # satisfies the request -- keep
    *)
      echo "venv interpreter $current does not satisfy requested $PYVER -- rebuilding $VENV"
      rm -rf "$VENV"
      ;;
  esac
fi

[[ -d "$VENV" ]] || uv venv --python "$PYVER" "$VENV"
uv pip install --python "$VENV/bin/python" -r "$REQ"
echo "venv ready: $VENV  (Python $(py_version "$VENV"), target=$TARGET)"
