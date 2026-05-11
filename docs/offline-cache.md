# Browser Offline Cache

tjai has two browser-side offline cache systems:

1. Research cache: a research-specific IndexedDB cache for `/tjai/research/`
   and research detail pages.
2. Generic material cache: a Cache Storage based warmer triggered from the
   diary page for diary, dashboard, goals, entries, relations, and menu pages.

They are intentionally separate. Research keeps its own data model and offline
rendering code. The generic cache is for first-class tjai pages and JSON
endpoints that can be replayed by the browser service worker.

## Generic Material Cache

Opening `/tjai/diary/` while online loads `/tjai/static/tjai/offline-cache.js`.
That script registers `/tjai/offline-sw.js` over the tjai page scopes, fetches
the manifest from `/tjai/api/offline/material-cache-manifest`, and warms only
manifest URLs missing from Cache Storage.

The generic cache uses these Cache Storage buckets:

| Cache | Purpose |
|-------|---------|
| `tjai-offline-v1:pages` | HTML page responses |
| `tjai-offline-v1:api` | JSON API responses |
| `tjai-offline-v1:static` | shared static assets needed by cached pages |

The service worker is network-first for navigations and `/tjai/api/` GETs:
online responses update the cache; offline or failed fetches fall back to a
cached response. Static files are cache-first.

The service worker does not delete old caches during activation. Cache eviction
is left to explicit future maintenance code or browser storage pressure.

## Manifest Contents

The manifest is built in `api_offline_material_cache_manifest` in
`tjai_app/views.py`. It returns a list of `{url, label, type, group}` items plus
group counts.

Current generic coverage includes:

- Diary page, diary entries API, and diary entry edit pages.
- Dashboard shell plus default calendar, named, status, and dialog count APIs.
- Daily synopsis list/content routes and the underlying daily entries.
- Workday, workweek, weekly, this-week, work highlights, and `@Underway`.
- Goals page, goals list API, every goal entry page, and every goal detail API.
- Todo entry pages.
- Named entries and journal entries.
- Relation APIs for cached entries, plus related entry pages and relation APIs
  for both sides of goal/todo relations.
- Main menu pages where offline shell loading is useful: assessment, git, dev,
  agent log, agent queue, picks, RSS, ReadMe, system, context, tag, and kind.

Entry pages are cached in both supported URL shapes when an `entry_id` exists:

- `/tjai/entry/?entry_id=<entry_id>`
- `/tjai/entry/<entry_id>/`

This matters because many page links use the human-readable path form, while
some editor and dashboard links use query parameters. Cache matching is exact
apart from the normalization described below, so both forms must be represented
or bridged.

## URL Normalization

Both the warmer and service worker normalize cache keys by stripping cache
buster query parameters:

- `_`
- `ts`

Other query parameters are significant. For example:

- `/tjai/api/goals/data?include_done=1` is distinct from
  `/tjai/api/goals/data`.
- `/tjai/entry/?entry_id=foo&edit=1` is distinct from
  `/tjai/entry/?entry_id=foo`.

The service worker also bridges human-readable entry paths to query-form cache
keys on offline fallback. A navigation to `/tjai/entry/foo/` can be satisfied
from a cached `/tjai/entry/?entry_id=foo` response; UUID-looking refs use
`?uuid=<uuid>`.

## Count Semantics

The diary cache status count is intended to reflect observed Cache Storage
state for the current manifest, not the number of successful fetches in the
current page load.

On each diary load:

1. The warmer fetches the current manifest.
2. It builds an index from actual Cache Storage keys in the generic buckets.
3. It counts manifest items whose normalized URLs are present.
4. It fetches only missing items.
5. It writes local metadata after the run with count, total, failed, bytes, and
   groups.

If the manifest fetch itself fails but previous metadata exists, the diary page
labels that as historical status with `last observed`. It is not a current
cache count.

## Offline Boundaries

The generic browser cache is read-through page/API caching, not a full client
database.

Expected offline behavior after warming:

- Cached page shells should load.
- Cached GET APIs should replay.
- Links whose exact URL form or service-worker alternate is cached should load.
- Entry editor assets are local static files.
- Existing entry autosave/localStorage behavior remains responsible for local
  draft preservation while offline.

Expected failures offline:

- POST/PUT/delete actions that require the server.
- Uncached filters, searches, pagination states, or query parameter variants.
- Newly-created server data that was never warmed on that browser.

## Files

Generic cache:

- `tjai_app/static/tjai/offline-cache.js`
- `tjai_app/static/tjai/offline-sw.js`
- `tjai_app/templates/tjai_app/diary.html`
- `tjai_app/views.py` - `api_offline_material_cache_manifest`
- `tjai_project/urls.py` - `/tjai/offline-sw.js` and manifest route

Research cache:

- `tjai_app/static/tjai/research-cache.js`
- `tjai_app/static/tjai/research-sw.js`
- `tjai_app/templates/tjai_app/research_list.html`
- `tjai_app/templates/tjai_app/research_detail.html`
- `tjai_app/views.py` - research list/detail APIs and service worker view

## Research Cache Behavior

Opening the research list or a research detail page registers
`/tjai/research-sw.js` and starts the passive research cache refresh.
`research-cache.js` stores the research list manifest and each topic detail
payload in IndexedDB database `tjai-research-cache`.

Each topic detail payload includes the topic, all model branch reports, and the
synthesis entry. The research warmer autodiscovers the individual entry page
links from those payloads:

- topic `entry_url`
- each model branch `entry_url`
- synthesis `entry_url`

It checks Cache Storage first and fetches only missing linked entry pages into
`tjai-research-shell-v1`. Already-cached detail payloads are still inspected so
newly-added or previously-missed linked entry pages are filled in on later
refreshes without refetching every detail payload.

The research cache does not cache Claude subagent studies pages. Those are a
separate drill-down view and are not part of the normal model-report/synthesis
reading path.

## Verification

Basic checks after changing the generic cache:

```bash
set -a && source /var/www/tjai/.env && set +a
source .venv/bin/activate
python manage.py check
node --check tjai_app/static/tjai/offline-cache.js
node --check tjai_app/static/tjai/offline-sw.js
python manage.py shell -c "from django.test import RequestFactory; from django.contrib.auth import get_user_model; from tjai_app.views import api_offline_material_cache_manifest; import json; r=RequestFactory().get('/tjai/api/offline/material-cache-manifest'); r.user=get_user_model().objects.filter(is_active=True).first(); data=json.loads(api_offline_material_cache_manifest(r).content); print(len(data['items']), data['groups'])"
```

After deploy, verify the live worker script parses and the health endpoint
responds:

```bash
node --check /var/www/tjai/tjai_app/static/tjai/offline-sw.js
curl -sS -o /dev/null -w '%{http_code} %{time_total}\n' https://etaverse.com/tjai/api/health
```
