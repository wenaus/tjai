"""Simple ANSI color utilities for tj."""

# ANSI color codes (optimized for dark mode)
MEDIUM_BLUE = '\033[38;5;111m'  # Medium blue for URLs
BRIGHT_CYAN_BLUE = '\033[38;5;81m'  # Bright cyan-blue for markdown link titles
LIGHT_MINT_GREEN = '\033[38;5;156m'  # Light mint green for tags
LIGHT_MAUVE = '\033[38;5;183m'  # Very light mauve for contexts
TERRACOTTA = '\033[38;5;216m'  # Terracotta for event timestamps (calendar)
SOFT_GREY = '\033[38;5;245m'  # Soft grey for creation timestamps
LIGHT_GOLD = '\033[38;5;221m'  # Light gold for kind brackets
BRIGHT_YELLOW = '\033[38;5;226m'  # Bright yellow for entry numbers
RED = '\033[38;5;203m'  # Red for next upcoming event countdown
BOLD = '\033[1m'  # Bold text
RESET = '\033[0m'

def colorize_url(text: str) -> str:
    """Colorize URLs and markdown links."""
    import re
    # First handle markdown links [title](url) - use iTerm2 hyperlink with clickable title
    # Format: \x1B]8;; (start) + URL + \x1B\\ (terminator) + title + \x1B]8;;\x1B\\ (end)
    md_link_pattern = r'\[([^\]]+)\]\((https?://[^\)]+)\)'
    def make_hyperlink(match):
        title = match.group(1)
        url = match.group(2)
        # Wrap hyperlink sequence in color: color + start link + URL + terminator + title + end link + reset
        return BRIGHT_CYAN_BLUE + '\x1B]8;;' + url + '\x1B\\' + title + '\x1B]8;;\x1B\\' + RESET
    text = re.sub(md_link_pattern, make_hyperlink, text)
    # Then handle bare URLs (but not those already in hyperlink escape sequences)
    # Use negative lookbehind to avoid matching URLs right after ]8;;
    url_pattern = r'(?<!]8;;)https?://[^\s]+'
    text = re.sub(url_pattern, f'{MEDIUM_BLUE}\\g<0>{RESET}', text)
    return text

def colorize_tags(text: str) -> str:
    """Colorize :tags in text with light mint green."""
    import re
    # Find :tag patterns and colorize them (must contain at least one letter)
    tag_pattern = r':[a-zA-Z][a-zA-Z0-9_-]*'
    return re.sub(tag_pattern, f'{LIGHT_MINT_GREEN}\\g<0>{RESET}', text)

def colorize_context(context: str) -> str:
    """Colorize context with very light mauve."""
    return f'{LIGHT_MAUVE}={context}{RESET}'

def colorize_kind(kind: str) -> str:
    """Colorize kind brackets with light gold."""
    return f'{LIGHT_GOLD}[{kind}]{RESET}'

def colorize_timestamp(timestamp: str) -> str:
    """Colorize event timestamp with terracotta."""
    return f'{TERRACOTTA}{timestamp}{RESET}'

def colorize_creation_timestamp(timestamp: str) -> str:
    """Colorize creation timestamp with soft grey."""
    return f'{SOFT_GREY}{timestamp}{RESET}'

def colorize_entry_number(number: int) -> str:
    """Colorize entry number with bright yellow and === prefix."""
    return f'{BRIGHT_YELLOW}==={number:3d}{RESET}'

def colorize_content(text: str) -> str:
    """Apply all content colorization."""
    text = colorize_url(text)
    text = colorize_tags(text)
    return text