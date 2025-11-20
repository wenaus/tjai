"""Repository abstraction layer for data access."""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Entry:
    """Data model for entries."""
    id: str
    content: str
    kind: str
    timestamp_created: float
    timestamp_modified: float
    parent_id: Optional[str] = None
    context: Optional[str] = None
    is_dirty: bool = True
    name: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


@dataclass
class Tag:
    """Data model for tags."""
    tag_name: str
    entry_id: str


@dataclass
class Context:
    """Data model for contexts.

    Contexts are specific projects, events, or activities, not broad categories.

    Fields:
    - name: terse nickname used in commands (e.g., "tjai", "hawaii2025")
    - title: optional short title via -t flag (e.g., "AI app", "Hawaii vacation")
    - description: optional longer description (remaining text after -t)

    Examples:
    - tj =tjai → name="tjai"
    - tj =tjai -t "AI app" → name="tjai", title="AI app"
    - tj =hawaii2025 Family trip to Maui → name="hawaii2025", description="Family trip to Maui"
    - tj =chep2024 -t "CHEP conference" San Francisco November 2024 → all three fields
    """
    name: str
    title: Optional[str]
    description: Optional[str]
    timestamp_created: float
    timestamp_modified: float


class EntryRepository(ABC):
    """Abstract repository interface for entries."""
    
    @abstractmethod
    def create_entry(self, entry: Entry) -> str:
        """Create a new entry and return its ID."""
        pass
    
    @abstractmethod
    def get_entry(self, entry_id: str) -> Optional[Entry]:
        """Get a single entry by ID."""
        pass
    
    @abstractmethod
    def update_entry(self, entry_id: str, **changes) -> bool:
        """Update an entry with the given changes."""
        pass
    
    @abstractmethod
    def delete_entry(self, entry_id: str) -> bool:
        """Delete an entry (soft delete)."""
        pass
    
    @abstractmethod
    def query_entries(self,
                     kind: Optional[str] = None,
                     context: Optional[str] = None,
                     tag: Optional[str] = None,
                     priority: Optional[int] = None,
                     status: Optional[str] = None,
                     limit: Optional[int] = None) -> List[Entry]:
        """Query entries with optional filters."""
        pass
    
    @abstractmethod
    def add_tag(self, entry_id: str, tag_name: str) -> None:
        """Add a tag to an entry."""
        pass
    
    @abstractmethod
    def remove_tag(self, entry_id: str, tag_name: str) -> bool:
        """Remove a specific tag from an entry."""
        pass
    
    @abstractmethod
    def remove_all_tag_instances(self, tag_name: str) -> int:
        """Remove all instances of a tag from the system. Returns count removed."""
        pass
    
    @abstractmethod
    def get_tags(self, entry_id: str) -> List[str]:
        """Get all tags for an entry."""
        pass
    
    @abstractmethod
    def get_all_tags(self) -> List[Tag]:
        """Get all tags in the system."""
        pass
    
    @abstractmethod
    def get_contexts(self) -> List[str]:
        """Get all context names that have been used in entries."""
        pass
    
    @abstractmethod
    def create_context(self, context: Context) -> bool:
        """Create a new context entity."""
        pass
    
    @abstractmethod
    def get_context(self, name: str) -> Optional[Context]:
        """Get a context by name."""
        pass
    
    @abstractmethod
    def get_all_contexts(self) -> List[Context]:
        """Get all context entities."""
        pass
    
    @abstractmethod
    def update_context(self, name: str, **changes) -> bool:
        """Update a context with the given changes."""
        pass

    @abstractmethod
    def delete_context(self, name: str) -> bool:
        """Delete a context by name."""
        pass