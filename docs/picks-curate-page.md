# Curate picks from the page you're viewing

A one-click path to curate picks from whatever authenticated page the user is
looking at — a CERN Indico agenda, a journal TOC, an internal wiki. The browser
extension is the only component that touches the source site; because it runs
inside the user's logged-in session it can read the page and (optionally) fetch
the page's same-origin file attachments. The tjai server never authenticates to
the source site.

## Two variants

- **Page only** — sends the page's extracted text. For pages whose DOM already
  carries enough (abstracts, summaries) or that have too many linked items to
  download. Works on any authenticated page.
- **With download** — the extension additionally fetches the page's same-origin
  PDFs (with the session cookie) and uploads them. For pages whose text is
  uninformative and whose substance lives in the attached slides.

## Determinism

The curation *process* is fixed in server code, not left to the model's
diligence. `scripts/curate_page.py` always loads the curation spec
(`scripts/curate_page_prompt.md`), the user profile, and the already-picked
URLs; runs the model; and creates the bookmark entries itself. The model
(Claude via `claude -p`, subscription auth, no API cost) only judges which items
earn a pick and writes each precis and rationale, returned as JSON. In
"with-download" mode the model reads the staged PDFs with the Read tool — the
slides are image-heavy and their titles are often useless, so multimodal reading
is required. `services.create_entry()`'s URL dedup is the backstop against
duplicates.

## Flow

1. Extension button → `POST /api/picks/curate-page` (Bearer `gmail_addon_api_key`,
   multipart: `url, title, source, mode, page_text, pdfs[]`).
2. The endpoint stages a job directory (`meta.json`, `page.txt`, `pdfs/`) and
   dispatches `curate_page.py` fire-and-forget, returning `{status:'queued', job_id}`.
3. The job curates and creates picks; they appear in the normal `/tjai/picks/`
   triage UI. Per-job status is `SysConfig['curate_<job_id>_status']`; a
   `result.json` (created/skipped) is written in the job directory.

## Components

| Piece | Path |
|---|---|
| Curation spec | `scripts/curate_page_prompt.md` |
| Background job | `scripts/curate_page.py` |
| Endpoint | `tjai_app/views.py` → `api_curate_page` |
| Route | `tjai_project/urls.py` → `api/picks/curate-page` |
| Extension buttons | `tjrepo/tj-getlink/` (page-only + with-download) |

## Deployment notes (ec2dev)

- Validate the `claude -p` flags that allow headless PDF reads without an
  interactive permission prompt (`curate_page.py` → `call_claude`,
  `--allowedTools Read`, `cwd=job_dir`); confirm headless PDF image extraction.
- Confirm the job-staging root is writable by the web user (defaults to
  `/var/www/tjai/data/curate-jobs`, falls back to the repo `data/` dir).
- Raise Django/Apache request-body limits for the with-download upload, matched
  to the server-side `MAX_PDFS` cap and the extension's warn/approve threshold.
