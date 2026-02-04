"""Persistent conversation storage using tjai entries."""

import logging
import time
import uuid

from tjai_app.models import Entry, Context, Tag

logger = logging.getLogger(__name__)

CONTEXT_NAME = 'tgbot'
CONVERSATION_TAG = 'tgchat'


def _ensure_context():
    """Ensure tgbot context exists."""
    context, created = Context.objects.get_or_create(
        name=CONTEXT_NAME,
        defaults={
            'title': 'Telegram Bot',
            'description': 'Telegram bot conversations and data',
            'timestamp_created': time.time(),
            'timestamp_modified': time.time(),
        }
    )
    if created:
        logger.info(f"Created context '{CONTEXT_NAME}'")
    return context


class PersistentConversationStore:
    """Conversation history persisted to tjai database."""

    def __init__(self, user_id: int, max_turns: int = 20):
        self.user_id = user_id
        self.max_turns = max_turns
        self._context = _ensure_context()
        self._load_history()

    def _load_history(self):
        """Load recent conversation history from database."""
        max_messages = self.max_turns * 2

        entries = Entry.objects.filter(
            kind='memory',
            context=self._context,
            deleted_at__isnull=True,
            tags__tag_name=CONVERSATION_TAG,
            data__telegram_user_id=self.user_id,
        ).order_by('-timestamp_created').distinct()[:max_messages]

        # Reverse to get chronological order
        entries_list = list(reversed(entries))

        self._history = []
        for entry in entries_list:
            if entry.data and 'role' in entry.data and entry.content:
                self._history.append({
                    'role': entry.data['role'],
                    'content': entry.content,
                })

        logger.info(f"Loaded {len(self._history)} conversation messages for user {self.user_id}")

    def _create_entry(self, role: str, text: str):
        """Create a conversation entry in the database."""
        now = time.time()
        entry_id = str(uuid.uuid4())

        entry = Entry.objects.create(
            id=entry_id,
            content=text,
            kind='memory',
            context=self._context,
            data={'role': role, 'telegram_user_id': self.user_id},
            timestamp_created=now,
            timestamp_modified=now,
            is_dirty=1,
        )
        Tag.objects.create(tag_name=CONVERSATION_TAG, entry=entry)

    def add_user(self, text: str):
        """Add a user message."""
        self._create_entry('user', text)
        self._history.append({'role': 'user', 'content': text})
        # Trim in-memory only (keep last max_turns*2)
        max_messages = self.max_turns * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

    def add_assistant(self, text: str):
        """Add an assistant message."""
        self._create_entry('assistant', text)
        self._history.append({'role': 'assistant', 'content': text})
        max_messages = self.max_turns * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

    def get_history(self) -> list:
        """Get conversation history for Claude API."""
        return list(self._history)

    def clear(self):
        """Clear in-memory history and reload from DB."""
        self._history.clear()
        logger.info(f"Cleared in-memory conversation for user {self.user_id}")


# Cache of conversation stores per user
_stores: dict[int, PersistentConversationStore] = {}


def get_conversation_store(user_id: int) -> PersistentConversationStore:
    """Get or create a persistent conversation store for a user."""
    if user_id not in _stores:
        _stores[user_id] = PersistentConversationStore(user_id)
    return _stores[user_id]
