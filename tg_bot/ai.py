"""Claude API integration with tjai tools."""

import json
import logging

import anthropic

from .config import Config
from .tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096
MAX_TOOL_ITERATIONS = 10


def build_system_prompt(profile_entries: list, ai_guidance: list) -> str:
    """Build system prompt with user context."""
    parts = [
        "You are Torre's personal AI assistant, accessible via Telegram.",
        "You have access to his tjai knowledge base - calendar, todos, memories, and more.",
        "Be concise and helpful. Torre prefers brief, direct responses without filler.",
        "",
    ]

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

    parts.extend([
        "## Tools Available",
        "You can read and create entries in tjai. Use tools to:",
        "- Check calendar (get_calendar)",
        "- View todos (get_todos)",
        "- Search memories (search_entries, get_memories)",
        "- Create new entries (create_entry) - memories, todos, calendar events",
        "- Mark todos done (edit_entry with status='done')",
        "",
        "When creating calendar events, use kind='journal' with event_date (YYYYMMDD) and event_time (HHMM).",
        "Today's tools operate in Eastern Time (America/New_York).",
    ])

    return "\n".join(parts)


class Assistant:
    """Claude assistant with tjai tool access."""

    def __init__(self):
        Config.validate()
        self.client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        self._system_prompt = None

    def _get_system_prompt(self) -> str:
        """Get or build system prompt with current user context."""
        if self._system_prompt is None:
            from .tools import get_profile, get_ai_guidance
            profile = get_profile()
            guidance = get_ai_guidance()
            self._system_prompt = build_system_prompt(profile, guidance)
        return self._system_prompt

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

        for iteration in range(MAX_TOOL_ITERATIONS):
            logger.debug(f"API call iteration {iteration + 1}")

            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=self._get_system_prompt(),
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )

            # Check if we need to handle tool use
            if response.stop_reason == "tool_use":
                # Process all tool calls in the response
                tool_results = []
                assistant_content = response.content

                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        tool_id = block.id

                        logger.info(f"Tool call: {tool_name}({json.dumps(tool_input)[:100]})")
                        result = execute_tool(tool_name, tool_input)
                        logger.debug(f"Tool result: {json.dumps(result)[:200]}")

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": json.dumps(result),
                        })

                # Add assistant message with tool use and tool results
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_results})

            else:
                # No more tool use, extract text response
                text_parts = []
                for block in response.content:
                    if hasattr(block, 'text'):
                        text_parts.append(block.text)

                return "\n".join(text_parts) if text_parts else "(no response)"

        logger.warning(f"Max tool iterations ({MAX_TOOL_ITERATIONS}) reached")
        return "I'm having trouble completing this request. Please try again."


# Singleton instance
_assistant = None


def get_assistant() -> Assistant:
    """Get or create assistant instance."""
    global _assistant
    if _assistant is None:
        _assistant = Assistant()
    return _assistant
