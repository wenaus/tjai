#!/usr/bin/env python3
"""Web search MCP server backed by SerpAPI.

Wraps SerpAPI's official JSON endpoint as a stdio MCP server. SerpAPI is
a commercial scraper of the major search engines (Google, Bing, Yahoo,
DuckDuckGo, YouTube, etc.) — paying them avoids both Google's
deprecation campaign against standalone search APIs and the principled
"do my own scraping" path.

We use a custom stdio wrapper rather than the upstream serpapi/serpapi-mcp
package because that package only supports HTTP/SSE transport
(uvicorn-served) and the worker's McpToolDispatcher only speaks stdio.
SerpAPI's REST API is one endpoint with query parameters returning
clean JSON, so a thin stdio wrapper is ~80 lines and we control it.

API docs: https://serpapi.com/search-api

Required environment variable:
    SERPAPI_API_KEY  — 64-char hex key from https://serpapi.com/manage-api-key

Run as stdio MCP server:
    python tj_agent/mcp_servers/web_search.py
"""

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API_KEY = os.environ.get("SERPAPI_API_KEY", "")
ENDPOINT = "https://serpapi.com/search.json"
DEFAULT_ENGINE = "google"

mcp = FastMCP(
    "web-search",
    instructions=(
        "General-purpose web search via SerpAPI. Use web_search to find "
        "URLs, titles, and snippets relevant to a query across the open "
        "web (Google by default, other engines on request). The snippet "
        "is short (1-2 sentences); to read a page in full, follow up "
        "with the fetch tool on the chosen URL. For searches restricted "
        "to the EIC / NPP software corpus use npp_search instead — it "
        "is faster and more focused for that subset."
    ),
)

_http = httpx.Client(timeout=30)


def _format_organic(items: list[dict], start: int) -> list[str]:
    out: list[str] = []
    for i, it in enumerate(items, start=start + 1):
        title = it.get("title", "(no title)")
        link = it.get("link", "")
        snippet = (it.get("snippet") or "").replace("\n", " ").strip()
        displayed = it.get("displayed_link", "")
        out.append(f"{i}. {title}")
        out.append(f"   {link}")
        if displayed and displayed not in link:
            out.append(f"   ({displayed})")
        if snippet:
            out.append(f"   {snippet}")
        out.append("")
    return out


@mcp.tool()
def web_search(
    query: str,
    num_results: int = 10,
    start: int = 0,
    engine: str = "google",
    location: str = "",
    gl: str = "",
    hl: str = "",
) -> str:
    """Search the open web via SerpAPI.

    Returns a numbered list of organic results: title, URL, snippet, and
    (when present) the answer box / featured snippet and the
    knowledge-graph entity card. To read any result in full, call the
    `fetch` tool with the URL.

    Args:
        query: The search query. Standard Google operators work in
            google engine (quoted phrases, -exclusions, site:domain,
            filetype:pdf, etc.).
        num_results: Number of organic results to return, 1-20. Default
            10.
        start: 0-based offset of the first result. Use to paginate
            through deeper results — set to 10 for the second page,
            20 for the third, etc.
        engine: Search engine to query. Defaults to "google". Other
            useful values: "bing", "duckduckgo", "yahoo", "google_news",
            "google_scholar", "youtube". Full list at
            https://serpapi.com/search-engine-apis.
        location: Optional location string for geo-localized results
            (e.g. "Long Island,New York,United States"). Most useful
            for google / google_news / google_maps.
        gl: Optional 2-letter country code (e.g. "us", "uk", "de").
            Affects ranking and which Google domain is used.
        hl: Optional 2-letter language code (e.g. "en", "es", "ja").
            Affects the interface language and result language.
    """
    if not API_KEY:
        return ("ERROR: SERPAPI_API_KEY is not set in the environment. "
                "Configure it in ~/.tjai/env and restart tj_agent.")
    if not query.strip():
        return "ERROR: query is empty"

    num = max(1, min(int(num_results), 20))
    start = max(0, int(start))

    params: dict[str, Any] = {
        "engine": engine or DEFAULT_ENGINE,
        "q": query,
        "num": num,
        "start": start,
        "api_key": API_KEY,
    }
    if location:
        params["location"] = location
    if gl:
        params["gl"] = gl
    if hl:
        params["hl"] = hl

    try:
        resp = _http.get(ENDPOINT, params=params)
    except httpx.HTTPError as e:
        return f"ERROR: HTTP request to SerpAPI failed: {type(e).__name__}: {e}"

    # SerpAPI returns either 200 with results, 200 with an "error" field
    # for plan/account problems, or non-200 with an error body.
    try:
        data = resp.json()
    except Exception as e:
        return (f"ERROR: SerpAPI returned non-JSON response "
                f"(HTTP {resp.status_code}): {resp.text[:500]}")

    if "error" in data:
        return f"ERROR: SerpAPI: {data['error']}"
    if resp.status_code != 200:
        return (f"ERROR: SerpAPI HTTP {resp.status_code}: "
                f"{data.get('error') or resp.text[:500]}")

    sm = data.get("search_metadata") or {}
    si = data.get("search_information") or {}
    total = si.get("total_results", "?")
    elapsed = sm.get("total_time_taken", "?")

    lines: list[str] = [
        f"Web search results for {query!r} via {engine} "
        f"(total={total}, time={elapsed}s, start={start}):",
        "",
    ]

    # Answer box / featured snippet (when google has one)
    ab = data.get("answer_box") or {}
    if ab:
        ab_title = ab.get("title") or ab.get("type") or "Answer box"
        ab_answer = (ab.get("answer") or ab.get("snippet")
                     or ab.get("result") or "")
        ab_link = ab.get("link", "")
        if ab_answer:
            lines.append(f"[Answer box: {ab_title}]")
            lines.append(f"  {ab_answer}")
            if ab_link:
                lines.append(f"  source: {ab_link}")
            lines.append("")

    # Knowledge graph card (entity info)
    kg = data.get("knowledge_graph") or {}
    if kg:
        kg_title = kg.get("title", "")
        kg_type = kg.get("type", "")
        kg_desc = kg.get("description", "")
        kg_source = (kg.get("source") or {}).get("link", "")
        if kg_title:
            header = f"[Knowledge graph: {kg_title}"
            if kg_type:
                header += f" ({kg_type})"
            header += "]"
            lines.append(header)
            if kg_desc:
                lines.append(f"  {kg_desc}")
            if kg_source:
                lines.append(f"  source: {kg_source}")
            lines.append("")

    # Organic results — the main course
    organic = data.get("organic_results") or []
    if organic:
        lines.append(f"[Organic results: {len(organic)}]")
        lines.extend(_format_organic(organic, start))
    elif not (ab or kg):
        lines.append(f"No results for query {query!r}.")

    # Pagination hint
    pagination = data.get("serpapi_pagination") or {}
    next_url = pagination.get("next")
    if next_url and len(organic) >= num:
        lines.append(
            f"(Call again with start={start + num} for the next page.)")

    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    mcp.run(transport="stdio")
