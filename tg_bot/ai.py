"""Claude API integration with tjai tools."""

import json
import logging

import anthropic

from .config import Config
from .tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
# 64_000 is Claude Sonnet 4.6's documented model max output for the
# synchronous Messages API (per Anthropic docs at
# platform.claude.com/docs/en/about-claude/models). The API requires
# max_tokens; using the model's own ceiling means the model — not this
# constant — decides how long the response is.
MAX_TOKENS = 64_000

# User location for web search (updated by location handler)
_user_location = None


def set_user_location(lat: float, lon: float):
    """Set user location from Telegram location share using reverse geocoding."""
    global _user_location

    # Use OpenStreetMap Nominatim for reverse geocoding (free, no API key needed)
    import urllib.request
    import json as _json

    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "tjai-bot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode())

        address = data.get("address", {})
        city = address.get("city") or address.get("town") or address.get("village") or address.get("county", "")
        region = address.get("state", "")
        country = address.get("country_code", "us").upper()

        _user_location = {
            "type": "approximate",
            "city": city,
            "region": region,
            "country": country,
        }
        logger.info(f"User location updated: {city}, {region}, {country} (from {lat}, {lon})")
    except Exception as e:
        logger.warning(f"Reverse geocoding failed: {e}")
        # Fallback: use coordinates to estimate - user is in ND area
        _user_location = {
            "type": "approximate",
            "city": "Williston",
            "region": "North Dakota",
            "country": "US",
        }
        logger.info(f"User location set to fallback: Williston, North Dakota")


def get_user_location():
    """Get current user location."""
    return _user_location


def _build_server_tools():
    """Build server tools with current location."""
    tools = [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        },
        {
            "type": "web_fetch_20250910",
            "name": "web_fetch",
            "max_uses": 3,
        },
    ]
    if _user_location:
        tools[0]["user_location"] = _user_location
        logger.info(f"Web search using location: {_user_location}")
    return tools


def build_system_prompt(profile_entries: list, ai_guidance: list) -> str:
    """Build system prompt with user context."""
    from .bot import get_voice_mode

    voice_mode = get_voice_mode()

    parts = [
        "You are Torre's personal AI assistant via Telegram, often used while driving.",
        "You have web_search and web_fetch tools - USE THEM for weather, news, current events, or any real-time information.",
        "You also have tjai tools for Torre's personal knowledge base.",
        "When creating tjai entries, do NOT add tags - the system auto-tags :fromai and :fromtg. Only add tags if the user explicitly requests them.",
    ]

    if voice_mode:
        parts.append("RESPONSE MODE: VOICE - Response will be spoken aloud. Keep it concise and conversational.")
    else:
        parts.append("RESPONSE MODE: TEXT - Response will be displayed as text. You can be more detailed.")

    # Add current GPS location - this overrides any profile residence data
    if _user_location:
        city = _user_location.get('city', '')
        region = _user_location.get('region', '')
        parts.append(f"Torre shared his Telegram GPS location. It was received and reverse-geocoded to: {city}, {region}. This IS the live Telegram location data. Use it for weather, local info, etc.")
    parts.append("")

    if profile_entries:
        parts.append("## About Torre")
        for entry in profile_entries[:10]:  # Limit to top 10
            parts.append(f"- {entry['content']}")
        parts.append("")

    if ai_guidance:
        parts.append("## Guidelines")
        for entry in ai_guidance[:10]:
            ctx = f" [{entry['context']}]" if entry.get('context') else ""
            parts.append(f"- {entry['content']}{ctx}")
        parts.append("")

    return "\n".join(parts)


class Assistant:
    """Claude assistant with tjai tool access."""

    def __init__(self):
        Config.validate()
        self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        self._system_prompt = None

    def _get_system_prompt(self) -> str:
        """Get or build system prompt with current user context."""
        # Always rebuild to include current location
        from .tools import get_profile, get_ai_guidance
        profile = get_profile()
        guidance = get_ai_guidance()
        return build_system_prompt(profile, guidance)

    def refresh_context(self):
        """Force refresh of system prompt."""
        self._system_prompt = None

    def chat(self, user_message: str, conversation_history: list = None) -> str:
        """
        Send a message and get a response, with tool use loop.

        Args:
            user_message: The user's message
            conversation_history: List of previous {"role": str, "content": str} messages

        Returns:
            Assistant's text response
        """
        messages = list(conversation_history or [])
        messages.append({"role": "user", "content": user_message})

        # Unbounded tool-use loop — the model decides when it's done by
        # emitting a non-tool-use stop_reason. No iteration cap.
        iteration = 0
        while True:
            iteration += 1
            logger.debug(f"API call iteration {iteration}")

            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self._get_system_prompt(),
                tools=_build_server_tools() + TOOL_DEFINITIONS,
                messages=messages,
                extra_headers={"anthropic-beta": "web-fetch-2025-09-10"},
            )
            # Log content blocks with details
            for block in response.content:
                if block.type == "server_tool_use":
                    logger.info(f"Server tool: {block.name}, input: {getattr(block, 'input', {})}")
                elif block.type == "web_search_tool_result":
                    # Log search results
                    results = getattr(block, 'content', [])
                    if results:
                        for r in results[:3]:  # First 3 results
                            logger.info(f"Search result: {getattr(r, 'title', '')[:50]} - {getattr(r, 'url', '')[:80]}")

            # Check if we need to handle tool use
            if response.stop_reason == "tool_use":
                # Process all tool calls in the response
                tool_results = []
                assistant_content = response.content

                for block in response.content:
                    if block.type == "tool_use":
                        # Custom tools (tjai) - we execute these
                        tool_name = block.name
                        tool_input = block.input
                        tool_id = block.id
                        logger.info(f"Tool call: {tool_name}({json.dumps(tool_input)[:200]})")

                        result = execute_tool(tool_name, tool_input)
                        logger.debug(f"Tool result: {json.dumps(result)[:200]}")

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": json.dumps(result),
                        })
                    # Server tools (web_search, web_fetch) are handled by Anthropic - no action needed

                # Only add tool results if we have any (server tools don't need results from us)
                if tool_results:
                    messages.append({"role": "assistant", "content": assistant_content})
                    messages.append({"role": "user", "content": tool_results})
                else:
                    # Server-only tools, continue the loop to get final response
                    messages.append({"role": "assistant", "content": assistant_content})

            else:
                # No more tool use, extract text response
                text_parts = []
                for block in response.content:
                    if hasattr(block, 'text'):
                        text_parts.append(block.text)

                return "\n".join(text_parts) if text_parts else "(no response)"


# Singleton instance
_assistant = None


def get_assistant() -> Assistant:
    """Get or create assistant instance."""
    global _assistant
    if _assistant is None:
        _assistant = Assistant()
    return _assistant
