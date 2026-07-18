# Authenticated CERN Indico access

`scripts/indico_fetch.py` provides programmatic read access to CERN Indico
(`indico.cern.ch`) content behind CERN SSO, from a machine whose Chrome is
already logged in to Indico. No CERN API token is required.

## Why this exists

Indico events are commonly CERN-SSO-protected: the HTML page 302-redirects to
`auth.cern.ch`, and the public HTTP export API returns `count: 0`. The tool
rides an existing browser login instead of provisioning a separate credential.

## How it works

- It reads **only** the `indico.cern.ch` cookies from the local Chrome
  profile using [`pycookiecheat`](https://pypi.org/project/pycookiecheat/),
  which decrypts Chrome's cookie store via the macOS Keychain "Chrome Safe
  Storage" key. The first run triggers a one-time Keychain **Allow** prompt.
  All cookies for that one domain are sent, and the read is scoped to that
  domain — not a blanket cookie-store dump. If `pycookiecheat` fails, the tool
  falls back to `browser_cookie3` for the same domain.
- **Key gotcha:** Indico's HTTP *export* API (`/export/...json`) does **not**
  honor browser session cookies — it authenticates API tokens only. So the tool
  targets the regular **web UI** and **file download** URLs, which the session
  cookie does unlock.

This is the access primitive. Higher-level workflows build on it — e.g. picks
curation over a workshop agenda: fetch the event overview HTML, extract
contribution titles/speakers and attachment (PDF) links, download the slides,
read them, and create picks.

## Dependency and setup (Mac-side only)

`pycookiecheat` needs the local authenticated browser, so this runs only on a
Mac with Chrome logged in to Indico. Install into a throwaway venv:

```bash
python3 -m venv .venv-indico
.venv-indico/bin/python -m pip install pycookiecheat
```

Optionally install `browser_cookie3` in the same venv; it is the fallback
cookie reader when `pycookiecheat` fails.

## Usage

```bash
# Event overview HTML
.venv-indico/bin/python scripts/indico_fetch.py 1680385 > event.html

# Any Indico URL or path; with an output file, bytes are written (e.g. a PDF)
.venv-indico/bin/python scripts/indico_fetch.py \
  /event/1680385/contributions/7063435/attachments/3288495/5879272/slides.pdf slides.pdf
```

A first argument beginning with `http` is fetched as given, and the Indico
cookie header is sent regardless of the host; use full URLs only for
`indico.cern.ch`.

Exit codes: `1` HTTP error, `2` bad args, `3` no cookie found (Chrome not logged
in), `4` redirected to SSO (session cookie not accepted for that URL).
