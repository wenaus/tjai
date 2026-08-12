# Page refresh

Pages built on server-side cached products (serve the stored build,
rebuild behind on access) go stale when unvisited: the store rebuilds
only on access, so the first visitor after a quiet stretch is served
the previous visitor's build. The `page-refresh` action keeps the
stored products of selected pages current by fetching each page's
synchronous-rebuild path on a fixed schedule, so a visitor at any hour
receives a product no older than the last scheduled refresh.

## Configuration

Two entries, both editable in the entry editor:

- **`page-refresh`** (kind=action, wrangler-owned) — the schedule and
  runner: `scheduled_time: 0600,1800` (ET), `mechanical_script:
  page_refresh.py`, `timeout: 900`.
- **`page-refresh-pages`** — the page list, one URL per line; other
  lines are ignored.

## Behavior

`scripts/page_refresh.py` fetches each listed URL with `refresh=1`
appended — on swf-monitor's cached-product pages this is the Update
path: the build runs synchronously and the response carries the fresh
product, so an HTTP 200 confirms the stored product is current.
Fetches are sequential with a 10-second gap, serializing load on the
serving host.

URLs under `https://epic-devcloud.org/prod` sit behind the swf-remote
login wall and are fetched as the `claude-ec2dev` Django service
account (credential file `/home/admin/.swf-remote-claude-ec2dev.env`;
also recorded in the ec2dev machine guidance entry). The script signs
in once per run. Other URLs are fetched anonymously.

Every listed page is attempted regardless of earlier failures. Any
failure exits the script nonzero with all failures listed, which fails
the wrangler worker and surfaces in AppLog and the Capcom feed.
Success emits no notice.
