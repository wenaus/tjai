"""Simple ANSI color utilities for tj."""

# ANSI color codes (optimized for dark mode)
CYAN = '\033[96m'  # Bright cyan for URLs - better on dark backgrounds
LIGHT_GREEN = '\033[92m'
RESET = '\033[0m'

def colorize_url(text: str) -> str:
    """Colorize URLs in text with bright cyan."""
    import re
    # Find URLs and colorize them
    url_pattern = r'https?://[^\s]+'
    return re.sub(url_pattern, f'{CYAN}\\g<0>{RESET}', text)

def colorize_tags(text: str) -> str:
    """Colorize :tags in text with light green."""
    import re
    # Find :tag patterns and colorize them
    tag_pattern = r':[a-zA-Z0-9_-]+'
    return re.sub(tag_pattern, f'{LIGHT_GREEN}\\g<0>{RESET}', text)

def colorize_content(text: str) -> str:
    """Apply all content colorization."""
    text = colorize_url(text)
    text = colorize_tags(text)
    return text