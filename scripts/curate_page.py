#!/usr/bin/env python3
"""Curate tjai picks from a single web page the user is viewing.

Deterministic counterpart to the scheduled picks-agent. The whole PROCESS lives
here in code — load the curation spec, load the profile, load the already-picked
URLs, run the model, create the entries — so it cannot be skipped or
mis-ordered. The model (Claude, via `claude -p`, subscription auth, no API cost)
does ONLY the judgement: which items earn a pick, plus the precis and rationale,
returned as JSON. This script creates the bookmark entries from that JSON via
services.create_entry(), whose URL dedup is the backstop.

The page content (and, in "with-download" mode, the slide PDFs the browser
extension fetched with the user's authenticated session) is staged on disk by
the /api/picks/curate-page endpoint, in a job directory:

    <job_dir>/meta.json   {"url","title","source","mode"}   (mode: page|download)
    <job_dir>/page.txt     extracted page text                (optional)
    <job_dir>/pdfs/*.pdf    downloaded slide decks             (download mode only)

Usage:
    python curate_page.py <job_dir>

Writes <job_dir>/result.json and sets SysConfig key
`curate_<job_id>_status` = running|completed|failed. Created picks land in the
`picks` collection and surface in the normal /tjai/picks/ triage UI.

HANDOFF (ec2dev — complete & deploy):
  * Validate the exact `claude -p` flags that let HEADLESS mode read the staged
    PDFs without an interactive permission prompt (see call_claude: --allowedTools
    "Read", cwd=job_dir). Confirm PDF image extraction works headless on the
    server the same way it does interactively.
  * Confirm the staging root the endpoint uses (see api_curate_page) and Apache
    upload-size limits for the download variant.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import bootstrap  # noqa: F401 - Django setup

from tjai_app.models import Entry, SysConfig
from tjai_app.services import create_entry

logger = logging.getLogger('curate_page')
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S'))
    logger.addHandler(_h)

CURATE_TIMEOUT = 1800  # 30 min — multi-PDF reads can be slow
PROMPT_PATH = Path(__file__).resolve().parent / 'curate_page_prompt.md'


def _find_claude():
    path = shutil.which('claude')
    if path:
        return path
    fallback = os.path.expanduser('~/.local/bin/claude')
    if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
        return fallback
    raise RuntimeError("'claude' CLI not found in PATH or ~/.local/bin")


def load_profile_text():
    """All profile facts as a plain-text block; rationales must connect to these."""
    rows = Entry.objects.filter(kind='profile', deleted_at__isnull=True) \
                        .order_by('-timestamp_modified')
    return '\n'.join(f'- {e.content.strip()}' for e in rows if e.content.strip())


def existing_pick_urls():
    """URLs already present in the picks collection (for the model's dedup awareness)."""
    urls = set()
    rows = Entry.objects.filter(kind='bookmark', context__name='picks',
                                deleted_at__isnull=True).only('content')
    for e in rows:
        m = re.search(r'\(\s*(https?://[^\s\)]+)\s*\)', e.content or '')
        if m:
            urls.add(m.group(1).strip())
    return urls


def build_prompt(spec, meta, page_text, pdf_paths, profile_text, picked_urls):
    parts = [spec, '\n\n===== PROFILE =====\n', profile_text or '(none)']
    parts.append('\n\n===== ALREADY PICKED (do not re-pick these URLs) =====\n')
    parts.append('\n'.join(sorted(picked_urls)) if picked_urls else '(none)')
    parts.append('\n\n===== PAGE =====\n')
    parts.append(f"url: {meta.get('url','')}\ntitle: {meta.get('title','')}\n")
    parts.append('\ntext:\n')
    parts.append(page_text.strip() if page_text else '(no text extracted from page)')
    parts.append('\n\n===== SLIDE PDFS =====\n')
    if pdf_paths:
        parts.append('Read each of these with the Read tool before judging:\n')
        parts.append('\n'.join(str(p) for p in pdf_paths))
    else:
        parts.append('(none — page-only mode; judge from the PAGE text above)')
    return ''.join(parts)


def call_claude(prompt, cwd):
    """Run `claude -p` (opus, subscription) with Read enabled so it can open the PDFs."""
    cmd = [
        _find_claude(), '-p',
        '--output-format', 'text',
        '--model', 'opus',
        '--effort', 'xhigh',
        # HANDOFF: confirm this is sufficient for headless PDF reads on ec2dev.
        '--allowedTools', 'Read',
    ]
    env = os.environ.copy()
    env.pop('CLAUDECODE', None)
    env.pop('ANTHROPIC_API_KEY', None)        # force subscription auth (no API cost)
    env['TJAI_ACTION_ID'] = 'picks-curate-page'  # don't record this as dialog
    logger.info("claude -p (opus, %d char prompt, cwd=%s)...", len(prompt), cwd)
    try:
        result = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                timeout=CURATE_TIMEOUT, env=env, cwd=str(cwd))
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude -p timed out after {CURATE_TIMEOUT}s")
    if result.returncode != 0:
        raise RuntimeError(f"claude -p exited {result.returncode}: "
                           f"{(result.stderr or '(no stderr)')[:500]}")
    if not result.stdout.strip():
        raise RuntimeError("claude -p returned empty output")
    return result.stdout


def parse_picks(output):
    """Pull the {"picks":[...]} object out of the model output, tolerant of fences/prose."""
    text = output.strip()
    if text.startswith('```'):
        text = re.sub(r'^```[a-zA-Z]*\n', '', text)
        text = re.sub(r'\n```\s*$', '', text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: brace-match the first {...} that contains "picks".
    start = text.find('{')
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            depth += (text[i] == '{') - (text[i] == '}')
            if depth == 0:
                blob = text[start:i + 1]
                if '"picks"' in blob:
                    return json.loads(blob)
                break
        start = text.find('{', start + 1)
    raise ValueError("no parseable picks JSON in model output")


def _slug(title):
    s = re.sub(r'[^a-z0-9]+', '-', (title or '').lower()).strip('-')
    return s[:60] or 'item'


def create_picks(picks, run_id, default_source):
    created, skipped = [], []
    for p in picks:
        url = (p.get('url') or '').strip()
        title = (p.get('title') or '').strip()
        if not url or not title:
            skipped.append({'title': title, 'url': url, 'reason': 'missing url/title'})
            continue
        res = create_entry(
            content=f'[{title}]({url})',
            kind='bookmark',
            context='picks',
            tags='fromai',
            data={
                'run': run_id,
                'source': p.get('source') or default_source,
                'precis': (p.get('precis') or '').strip(),
                'rationale': (p.get('rationale') or '').strip(),
                'entry_id': f'picks-curate-{_slug(title)}',
            },
        )
        if isinstance(res, dict) and res.get('error'):
            skipped.append({'title': title, 'url': url, 'reason': res['error']})
        else:
            created.append({'title': title, 'url': url})
    return created, skipped


def _set_status(job_id, status):
    SysConfig.objects.update_or_create(
        key=f'curate_{job_id}_status',
        defaults={'value': status, 'timestamp_modified': time.time()},
    )


def main(job_dir):
    job_dir = Path(job_dir).resolve()
    job_id = job_dir.name
    _set_status(job_id, 'running')
    start = time.time()
    try:
        meta = json.loads((job_dir / 'meta.json').read_text(encoding='utf-8'))
        page_file = job_dir / 'page.txt'
        page_text = page_file.read_text(encoding='utf-8') if page_file.exists() else ''
        pdf_dir = job_dir / 'pdfs'
        pdf_paths = sorted(pdf_dir.glob('*.pdf')) if pdf_dir.is_dir() else []

        host = urlparse(meta.get('url', '')).netloc or 'page'
        run_id = f"curate:{host}:{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        source = meta.get('source') or meta.get('title') or host

        spec = PROMPT_PATH.read_text(encoding='utf-8')
        prompt = build_prompt(spec, meta, page_text, pdf_paths,
                              load_profile_text(), existing_pick_urls())

        output = call_claude(prompt, cwd=job_dir)
        (job_dir / 'model_output.txt').write_text(output, encoding='utf-8')

        parsed = parse_picks(output)
        picks = parsed.get('picks', []) if isinstance(parsed, dict) else []
        created, skipped = create_picks(picks, run_id, source)

        result = {
            'status': 'completed', 'job_id': job_id, 'run': run_id,
            'mode': meta.get('mode'), 'pdf_count': len(pdf_paths),
            'created': created, 'skipped': skipped,
            'duration_seconds': round(time.time() - start),
        }
        (job_dir / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        _set_status(job_id, 'completed')
        logger.info("curate done: %d created, %d skipped (%ds)",
                    len(created), len(skipped), result['duration_seconds'])
    except Exception as e:
        logger.error("curate failed: %s\n%s", e, traceback.format_exc())
        try:
            (job_dir / 'result.json').write_text(
                json.dumps({'status': 'failed', 'error': str(e)}, indent=2),
                encoding='utf-8')
        except Exception:
            pass
        _set_status(job_id, 'failed')
        sys.exit(1)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("usage: curate_page.py <job_dir>", file=sys.stderr)
        sys.exit(2)
    main(sys.argv[1])
