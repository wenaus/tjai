#!/usr/bin/env python3
"""NPP software search via Google Programmable Search Engine.

Wraps Google's Custom Search JSON API as a stdio MCP server, but with
the search engine ID configured to a Programmable Search Engine that is
RESTRICTED to the corpus of nuclear & particle physics software sites
Torre actually works in. Not a general open-web search.

Sites covered by the configured CSE (set in the Programmable Search
Engine console — change there, not here):

    eic-code-browser.sdcc.bnl.gov   — LXR cross-reference of 55+ EIC repos
    eic.github.io                    — ePIC Software & Computing GitHub Pages
    eicrecon.epic-eic.org            — EICrecon documentation
    panda-wms.readthedocs.io         — PanDA workload management docs
    idds.readthedocs.io              — iDDS docs

Use this for semantic, Google-ranked search across that corpus. For
exact-symbol or exact-text matches against the LXR live tree use the
lxr_ident / lxr_search tools (lxr-mcp-server) instead — those go
straight against LXR's own index, not Google's snapshot.

API docs: https://developers.google.com/custom-search/v1/using_rest

Required environment variables:
    GOOGLE_CSE_API_KEY  — Google Cloud Console API key with the
                          "Custom Search API" enabled.
    GOOGLE_CSE_CX       — Programmable Search Engine ID created at
                          https://programmablesearchengine.google.com,
                          configured with the site list above.

Run as stdio MCP server:
    python tj_agent/mcp_servers/npp_search.py
"""

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API_KEY = os.environ.get("GOOGLE_CSE_API_KEY", "")
CX = os.environ.get("GOOGLE_CSE_CX", "")
ENDPOINT = "https://www.googleapis.com/customsearch/v1"

mcp = FastMCP(
    "npp-search",
    instructions=(
        "Google-ranked semantic search restricted to the nuclear & "
        "particle physics software corpus: EIC LXR code browser, "
        "ePIC Software & Computing GitHub Pages, EICrecon docs, "
        "PanDA workload management docs, iDDS docs. "
        "Use npp_search to find URLs, titles, and snippets relevant "
        "to a query. The snippet is short (1-2 sentences); to read a "
        "page in full, follow up with the fetch tool on the chosen URL. "
        "For exact-symbol or exact-text matches against EIC source "
        "files, use the lxr_ident / lxr_search tools instead — those "
        "go directly against LXR's live index, not Google's snapshot."
    ),
)

_http = httpx.Client(timeout=20)


@mcp.tool()
def npp_search(
    query: str,
    num_results: int = 10,
    start: int = 1,
    site: str = "",
    date_restrict: str = "",
) -> str:
    """Search the nuclear & particle physics software corpus.

    Returns a numbered list of results: title, URL, and a 1-2 sentence
    snippet for each. To read any result in full, call the `fetch`
    tool with the URL.

    The corpus is fixed by the Programmable Search Engine config and
    currently covers:
        - eic-code-browser.sdcc.bnl.gov  (LXR, 55+ EIC repos)
        - eic.github.io                  (ePIC S&C docs, GitHub Pages)
        - eicrecon.epic-eic.org          (EICrecon documentation)
        - panda-wms.readthedocs.io       (PanDA WMS docs)
        - idds.readthedocs.io            (iDDS docs)

    Args:
        query: The search query. Standard Google operators work
            (quoted phrases, -exclusions, filetype:pdf, etc.).
        num_results: Number of results to return, 1-10. Default 10
            (the per-call maximum the CSE API allows).
        start: 1-based index of the first result to return. Use to
            paginate through deeper results — set to 11 to get the
            second page, 21 for the third, etc. Max start index is 91.
        site: Optional. Further narrow results to a single domain
            already in the corpus, e.g. "panda-wms.readthedocs.io"
            to restrict to PanDA docs only. Useless if the domain
            isn't in the configured CSE site list.
        date_restrict: Optional. Restrict by recency. Values:
            "d<N>" last N days, "w<N>" last N weeks,
            "m<N>" last N months, "y<N>" last N years.
            E.g. "m6" = past 6 months. Empty string = no restriction.
    """
    if not API_KEY or not CX:
        return ("ERROR: GOOGLE_CSE_API_KEY and/or GOOGLE_CSE_CX not set "
                "in the environment. The MCP server cannot reach the "
                "Google CSE API without both. Configure them in "
                "~/.tjai/env and restart tj_agent.")

    if not query.strip():
        return "ERROR: query is empty"

    num = max(1, min(int(num_results), 10))
    start = max(1, min(int(start), 91))

    params: dict[str, Any] = {
        "key": API_KEY,
        "cx": CX,
        "q": query,
        "num": num,
        "start": start,
    }
    if site:
        params["siteSearch"] = site
        params["siteSearchFilter"] = "i"  # include
    if date_restrict:
        params["dateRestrict"] = date_restrict

    try:
        resp = _http.get(ENDPOINT, params=params)
    except httpx.HTTPError as e:
        return f"ERROR: HTTP request failed: {type(e).__name__}: {e}"

    if resp.status_code != 200:
        # CSE returns JSON error bodies; surface them verbatim so the
        # caller can see quota / auth / config issues directly
        try:
            err = resp.json().get("error", {})
            msg = err.get("message", "")
            code = err.get("code", resp.status_code)
            return f"ERROR: CSE API {code}: {msg}"
        except Exception:
            return f"ERROR: CSE API HTTP {resp.status_code}: {resp.text[:500]}"

    data = resp.json()

    # search_information has totalResults, searchTime
    info = data.get("searchInformation", {}) or {}
    total = info.get("formattedTotalResults") or info.get("totalResults", "?")
    elapsed = info.get("formattedSearchTime") or info.get("searchTime", "?")

    items = data.get("items", []) or []
    if not items:
        return (f"No results in NPP corpus for query {query!r} "
                f"(total={total}, time={elapsed}s).")

    lines = [
        f"NPP search results for {query!r} "
        f"(total={total}, time={elapsed}s, showing {len(items)} from start={start}):",
        "",
    ]
    for i, item in enumerate(items, start=start):
        title = item.get("title", "(no title)")
        link = item.get("link", "")
        snippet = (item.get("snippet") or "").replace("\n", " ").strip()
        lines.append(f"{i}. {title}")
        lines.append(f"   {link}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")

    # If there's a next page available, hint at how to get it
    next_start = start + len(items)
    queries_meta = data.get("queries", {}) or {}
    if queries_meta.get("nextPage") and next_start <= 91:
        lines.append(
            f"(Call again with start={next_start} for the next page.)")

    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    mcp.run(transport="stdio")
