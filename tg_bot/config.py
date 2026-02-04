"""Configuration loading for tg_bot."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from /var/www/tjai/ (production) or local dev
ENV_PATHS = [
    Path('/var/www/tjai/.env'),
    Path(__file__).parent.parent / '.env',
]

for env_path in ENV_PATHS:
    if env_path.exists():
        load_dotenv(env_path)
        break


class Config:
    TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    TELEGRAM_USER_ID = os.getenv('TELEGRAM_USER_ID')
    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

    # Django database URL for direct ORM access
    DATABASE_URL = os.getenv('DJANGO_DATABASE_URL')

    @classmethod
    def validate(cls):
        """Validate required configuration."""
        missing = []
        if not cls.TELEGRAM_BOT_TOKEN:
            missing.append('TELEGRAM_BOT_TOKEN')
        if not cls.TELEGRAM_USER_ID:
            missing.append('TELEGRAM_USER_ID')
        if not cls.ANTHROPIC_API_KEY:
            missing.append('ANTHROPIC_API_KEY')
        if missing:
            raise ValueError(f"Missing required config: {', '.join(missing)}")
        return True

    @classmethod
    def user_id_int(cls):
        """Return TELEGRAM_USER_ID as integer."""
        return int(cls.TELEGRAM_USER_ID) if cls.TELEGRAM_USER_ID else None
