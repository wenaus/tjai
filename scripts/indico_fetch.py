#!/usr/bin/env python3
"""Authenticated CERN Indico fetcher — reuse a logged-in browser's session.

General capability: programmatic read access to CERN Indico (indico.cern.ch)
events, pages, and attachments that are behind CERN SSO, from a machine whose
Chrome is already logged in to Indico. No CERN API token required.

How it works
------------
- Pulls ONLY the indico.cern.ch session cookie from the local Chrome profile via
  `pycookiecheat` (which decrypts Chrome's cookie store using the macOS Keychain
  "Chrome Safe Storage" key — a one-time Keychain "Allow" prompt). Scoped to the
  one domain; never a blanket cookie-store dump.
- IMPORTANT GOTCHA: Indico's HTTP *export* API (`/export/...json`) does NOT honor
  browser session cookies — it only authenticates API tokens, and returns
  `count: 0` for protected events when unauthenticated. So this tool hits the
  regular web UI / file download URLs, which the session cookie DOES unlock.

This is the access primitive. Applications (e.g. the tjai picks-curation
workflow over a CERN workshop agenda) build on top of it.

Dependency: `pycookiecheat` (Mac-side only — needs the local authenticated
browser). Install into a throwaway venv; see docs/indico-access.md.

Usage
-----
  python3 indico_fetch.py <event_id>            # -> event overview HTML to stdout
  python3 indico_fetch.py <url-or-path> [out]   # fetch any indico URL/path;
                                                # with [out], write bytes to file
Examples
  python3 indico_fetch.py 1680385 > event.html
  python3 indico_fetch.py /event/1680385/contributions/7063435/attachments/3288495/5879272/x.pdf slides.pdf
"""
import sys, urllib.request, urllib.error

BASE = "https://indico.cern.ch"


def get_indico_cookie_header():
    """Return a Cookie header string for indico.cern.ch from the local Chrome."""
    cookies = {}
    try:
        from pycookiecheat import chrome_cookies
        cookies = chrome_cookies(BASE)
    except Exception as e:
        print(f"[pycookiecheat] {e}", file=sys.stderr)
        try:
            import browser_cookie3
            cj = browser_cookie3.chrome(domain_name="indico.cern.ch")
            cookies = {ck.name: ck.value for ck in cj}
        except Exception as e2:
            print(f"[browser_cookie3] {e2}", file=sys.stderr)
    print(f"cookie names: {sorted(cookies.keys())}", file=sys.stderr)
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    arg = sys.argv[1]
    outfile = sys.argv[2] if len(sys.argv) > 2 else None

    if arg.isdigit():
        url = f"{BASE}/event/{arg}/"
    elif arg.startswith("http"):
        url = arg
    else:
        url = BASE + (arg if arg.startswith("/") else "/" + arg)

    cookie_header = get_indico_cookie_header()
    if not cookie_header:
        print("NO COOKIES — is Chrome logged in to indico.cern.ch?", file=sys.stderr)
        sys.exit(3)

    req = urllib.request.Request(
        url, headers={"Cookie": cookie_header, "User-Agent": "Mozilla/5.0 tjai-indico-fetch"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            final = r.geturl()
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} for {url}: {e.read()[:300]}", file=sys.stderr)
        sys.exit(1)

    print(f"fetched {url}  ->  final={final}  bytes={len(body)}", file=sys.stderr)
    if "auth.cern.ch" in final:
        print("REDIRECTED TO SSO — session cookie not accepted for this URL", file=sys.stderr)
        sys.exit(4)

    if outfile:
        with open(outfile, "wb") as f:
            f.write(body)
        print(f"wrote {len(body)} bytes -> {outfile}", file=sys.stderr)
    else:
        sys.stdout.buffer.write(body)


if __name__ == "__main__":
    main()
