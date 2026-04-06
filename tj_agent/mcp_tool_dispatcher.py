"""MCP tool dispatcher for the remote inference worker.

Spawns one or more MCP servers as stdio subprocesses, holds long-lived
client sessions to each, discovers their tools, and exposes the union of
those tools to the worker in the format ollama's /api/chat `tools`
parameter expects. Dispatches each tool call back to whichever MCP
server owns the named tool.

Usage:
    dispatcher = McpToolDispatcher()
    await dispatcher.start([
        ServerSpec("lxr",    sys.executable, ["lxr_mcp_server.py"], cwd=...),
        ServerSpec("github", "github-mcp-server", ["stdio"], env={...}),
    ])
    tools = dispatcher.tools_for_ollama()  # list[dict] in OpenAI/ollama format
    result = await dispatcher.call_tool("lxr_ident", {"symbol": "Foo"})
    await dispatcher.close()

The class is async-native; the worker drives it from inside an asyncio
loop (run_worker_forever now wraps an async coroutine).
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ServerSpec:
    """How to launch one MCP server as a stdio subprocess."""
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


@dataclass
class _ToolEntry:
    """One tool we discovered, plus the session that owns it."""
    name: str
    server_name: str
    description: str
    input_schema: dict[str, Any]


class McpToolDispatcher:
    """Holds MCP client sessions and routes tool calls to the right one.

    All public methods are async — the worker runs them inside an asyncio
    loop. The agent does NOT hide the fact that MCP startup can fail: if a
    server can't launch, that server's tools simply aren't available, but
    the rest of the agent (and the worker) keeps running. Each failure is
    logged loudly.
    """

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._sessions: dict[str, Any] = {}  # name -> ClientSession
        self._tools: list[_ToolEntry] = []
        self._tool_owner: dict[str, str] = {}  # tool name -> session name

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def server_names(self) -> list[str]:
        return sorted(self._sessions.keys())

    async def start(self, specs: list[ServerSpec]) -> None:
        """Connect to all MCP servers and discover their tools.

        Servers that fail to start are logged and skipped. The agent ends
        up with whatever subset of servers actually came up successfully.
        """
        # Lazy import — keeps mcp out of the import path for users who
        # don't enable the worker
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()

        for spec in specs:
            try:
                params = StdioServerParameters(
                    command=spec.command,
                    args=spec.args,
                    env=spec.env,
                    cwd=spec.cwd,
                )
                # stdio_client is an async context manager yielding (read, write)
                read, write = await self._stack.enter_async_context(
                    stdio_client(params))
                session = await self._stack.enter_async_context(
                    ClientSession(read, write))
                await session.initialize()

                tools_resp = await session.list_tools()
                tool_names = []
                for t in tools_resp.tools:
                    if t.name in self._tool_owner:
                        logger.warning(
                            "mcp_agent: tool %r already provided by %r — "
                            "ignoring duplicate from %r",
                            t.name, self._tool_owner[t.name], spec.name)
                        continue
                    schema = t.inputSchema or {"type": "object", "properties": {}}
                    self._tools.append(_ToolEntry(
                        name=t.name,
                        server_name=spec.name,
                        description=t.description or "",
                        input_schema=schema,
                    ))
                    self._tool_owner[t.name] = spec.name
                    tool_names.append(t.name)

                self._sessions[spec.name] = session
                logger.info(
                    "mcp_agent: %s — %d tools (%s)",
                    spec.name, len(tool_names),
                    ", ".join(tool_names) if tool_names else "(none)")
            except Exception as e:
                logger.exception(
                    "mcp_agent: failed to start MCP server %r (%s %s): %s",
                    spec.name, spec.command, spec.args, e)
                # Continue with other servers

        logger.info(
            "mcp_agent: ready — %d server(s), %d tool(s)",
            len(self._sessions), len(self._tools))

    def tools_for_ollama(self) -> list[dict[str, Any]]:
        """Return all discovered tools in the format ollama /api/chat expects.

        Ollama uses the OpenAI-compatible tool schema:
            {"type": "function",
             "function": {"name": "...", "description": "...",
                          "parameters": <json-schema>}}
        """
        out: list[dict[str, Any]] = []
        for t in self._tools:
            out.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            })
        return out

    async def call_tool(self, name: str, arguments: dict | str) -> str:
        """Dispatch a tool call to whichever MCP server owns this tool.

        `arguments` may arrive as a JSON string or a dict (ollama is
        inconsistent here). Returns a string suitable for putting in a
        chat "tool" message — concatenates all text content blocks.
        Raises if the tool is unknown or the server errors.
        """
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"tool {name!r} got invalid JSON arguments: {e}")
        if not isinstance(arguments, dict):
            raise ValueError(
                f"tool {name!r} arguments must be dict, got {type(arguments).__name__}")

        owner = self._tool_owner.get(name)
        if not owner:
            raise KeyError(
                f"unknown tool {name!r}; known: {sorted(self._tool_owner.keys())}")
        session = self._sessions.get(owner)
        if not session:
            raise RuntimeError(
                f"tool {name!r} owner {owner!r} has no live session")

        result = await session.call_tool(name, arguments=arguments)

        # Concatenate text content blocks. MCP can also return images,
        # resources, etc.; for the worker we only care about text for now.
        parts: list[str] = []
        for block in (result.content or []):
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
            else:
                # Fall back to repr so the model at least sees *something*
                parts.append(repr(block))
        text_out = "\n".join(parts).strip()

        if getattr(result, "isError", False):
            return f"TOOL ERROR: {text_out}"
        return text_out or "(empty result)"

    async def close(self) -> None:
        """Tear down all MCP sessions and subprocesses."""
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as e:
                logger.warning("mcp_agent: error during close: %s", e)
            self._stack = None
        self._sessions.clear()
        self._tools.clear()
        self._tool_owner.clear()


def default_server_specs() -> list[ServerSpec]:
    """Return the default set of MCP servers the worker should spawn.

    Each entry is conditional on its prerequisites being available — a
    missing binary or env var causes that server to be skipped silently
    (a warning is logged when start() actually tries to launch it).

    Configurable via env vars:
        LXR_MCP_SERVER_PATH    — path to lxr_mcp_server.py
                                 (default: ~/github/lxr-mcp-server/lxr_mcp_server.py)
        GITHUB_MCP_SERVER_BIN  — path to the github-mcp-server binary
                                 (default: looks in PATH and ~/bin/)
        GITHUB_PERSONAL_ACCESS_TOKEN — required for github-mcp-server
        GOOGLE_CSE_API_KEY     — required for npp_search (Google Cloud
                                 console API key with Custom Search API
                                 enabled)
        GOOGLE_CSE_CX          — required for npp_search (Programmable
                                 Search Engine ID from
                                 programmablesearchengine.google.com,
                                 configured with the NPP-software site
                                 list)
        SERPAPI_API_KEY        — required for web_search (64-char hex
                                 key from https://serpapi.com)
    """
    import shutil
    import sys
    from pathlib import Path

    specs: list[ServerSpec] = []

    # ── lxr-mcp-server (Python, stdio) ──────────────────────────────────
    lxr_path = os.environ.get(
        "LXR_MCP_SERVER_PATH",
        str(Path.home() / "github" / "lxr-mcp-server" / "lxr_mcp_server.py"),
    )
    if Path(lxr_path).exists():
        specs.append(ServerSpec(
            name="lxr",
            command=sys.executable,
            args=[lxr_path],
            cwd=str(Path(lxr_path).parent),
        ))
    else:
        logger.warning(
            "mcp_agent: lxr-mcp-server not found at %s; skipping", lxr_path)

    # ── github-mcp-server (Go binary, stdio) ────────────────────────────
    gh_bin = os.environ.get("GITHUB_MCP_SERVER_BIN")
    if not gh_bin:
        # Try standard locations
        for candidate in (
            shutil.which("github-mcp-server"),
            str(Path.home() / "bin" / "github-mcp-server"),
        ):
            if candidate and Path(candidate).exists():
                gh_bin = candidate
                break

    gh_token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if gh_bin and gh_token:
        specs.append(ServerSpec(
            name="github",
            command=gh_bin,
            args=["stdio"],
            env={
                # github-mcp-server reads its token from this env var
                "GITHUB_PERSONAL_ACCESS_TOKEN": gh_token,
                # Pass through PATH so the binary can find tools it needs
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
            },
        ))
    else:
        if not gh_bin:
            logger.warning(
                "mcp_agent: github-mcp-server binary not found; skipping")
        elif not gh_token:
            logger.warning(
                "mcp_agent: GITHUB_PERSONAL_ACCESS_TOKEN not set; skipping github")

    # ── mcp-server-fetch (Python, stdio) — official URL fetcher ─────────
    # First-party from modelcontextprotocol/servers. Installed via
    # `pip install mcp-server-fetch` into the tj_agent venv. No env vars,
    # no credentials, no third-party service — just retrieves a URL and
    # returns cleaned markdown. Honors robots.txt by default.
    specs.append(ServerSpec(
        name="fetch",
        command=sys.executable,
        args=["-m", "mcp_server_fetch"],
    ))

    # ── npp_search (custom Python, stdio) — NPP-software corpus search ──
    # Wraps Google's Custom Search JSON API as a stdio MCP server, with
    # the engine ID configured to a CSE restricted to a curated list of
    # nuclear & particle physics software sites (LXR, ePIC S&C, EICrecon,
    # PanDA, iDDS, ...). Skipped silently if either credential is missing
    # — set both in ~/.tjai/env to enable.
    cse_key = os.environ.get("GOOGLE_CSE_API_KEY")
    cse_cx = os.environ.get("GOOGLE_CSE_CX")
    if cse_key and cse_cx:
        npp_path = (Path(__file__).parent / "mcp_servers" / "npp_search.py")
        if npp_path.exists():
            specs.append(ServerSpec(
                name="npp_search",
                command=sys.executable,
                args=[str(npp_path)],
                env={
                    "GOOGLE_CSE_API_KEY": cse_key,
                    "GOOGLE_CSE_CX": cse_cx,
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": os.environ.get("HOME", ""),
                },
            ))
        else:
            logger.warning(
                "mcp_agent: npp_search.py not found at %s; skipping",
                npp_path)
    else:
        missing = []
        if not cse_key:
            missing.append("GOOGLE_CSE_API_KEY")
        if not cse_cx:
            missing.append("GOOGLE_CSE_CX")
        logger.warning(
            "mcp_agent: %s not set; skipping npp_search",
            " and ".join(missing))

    # ── web_search (custom Python, stdio) — SerpAPI general web search ──
    # Wraps SerpAPI's REST API as a stdio MCP server. Custom rather than
    # the upstream serpapi/serpapi-mcp because that package only supports
    # HTTP transport (uvicorn-served) and our dispatcher only speaks
    # stdio. Skipped silently if SERPAPI_API_KEY is not set.
    serpapi_key = os.environ.get("SERPAPI_API_KEY")
    if serpapi_key:
        web_path = (Path(__file__).parent / "mcp_servers" / "web_search.py")
        if web_path.exists():
            specs.append(ServerSpec(
                name="web_search",
                command=sys.executable,
                args=[str(web_path)],
                env={
                    "SERPAPI_API_KEY": serpapi_key,
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": os.environ.get("HOME", ""),
                },
            ))
        else:
            logger.warning(
                "mcp_agent: web_search.py not found at %s; skipping",
                web_path)
    else:
        logger.warning(
            "mcp_agent: SERPAPI_API_KEY not set; skipping web_search")

    return specs
