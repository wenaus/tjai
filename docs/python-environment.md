# Python Environment

The interpreter and dependency set are defined entirely by version-controlled
files. No interpreter path, package, or version lives in a person's memory, in
an ad-hoc `pip install`, or hardcoded in a script. Dev and prod are built by the
same code path from the same files, so they cannot drift.

## Sources of truth

| Element | File | Notes |
|---------|------|-------|
| Interpreter version | `.python-version` | One line (e.g. `3.14.3`). The de-facto standard pin, read by uv. |
| Base dependencies | `requirements/base.txt` | The Django app. |
| Telegram bot deps | `requirements/tgbot.txt` | Composed into prod and dev; not installed standalone. |
| Prod dependency set | `requirements/prod.txt` | `base` + `tgbot` + server tooling (gunicorn, py-spy, supervisor). |
| Dev dependency set | `requirements/dev.txt` | `base` + `tgbot` (prod minus server tooling). |
| Build procedure | `deploy/make_venv.sh` | The only code path that creates a venv. |

Version ranges are `>=` by intent — newest compatible releases, no lockfile.

## Provisioning: uv

[uv](https://docs.astral.sh/uv/) reads `.python-version`, fetches that exact
standalone CPython if it is not already present, creates the venv, and installs
the requirements. There is no hand-compiled interpreter to maintain.

```bash
deploy/make_venv.sh                 # dev venv at ./.venv, requirements/dev.txt
deploy/make_venv.sh /path/.venv prod  # prod venv, requirements/prod.txt
```

`make_venv.sh` is self-healing: if an existing venv's interpreter no longer
matches `.python-version`, it rebuilds rather than installing into the stale
interpreter. Bumping the Python version is therefore a one-line edit to
`.python-version` — the next venv build picks it up automatically.

uv-managed venvs do not seed `pip`; use `uv pip install --python <venv>/bin/python`
to add a package to an existing venv.

## Deployment

`deploy/update_from_dev.sh` calls `make_venv.sh` for the prod target whenever any
requirements file or `.python-version` changes (tracked by a content hash).
Service files reference `/var/www/tjai/.venv/bin/python`, a path stable across
rebuilds, so they need no changes when the interpreter version moves.

## Bumping the interpreter

1. Edit `.python-version` to the new version.
2. Run `deploy/make_venv.sh` to rebuild the dev venv and confirm the app works.
3. Deploy — `update_from_dev.sh` detects the change and rebuilds the prod venv.
