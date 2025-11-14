"""Factory for creating repository instances."""

import os
from typing import Optional

from tj.repository import EntryRepository
from tj.repository_sqlite import SQLiteRepository


class RepositoryFactory:
    """Factory for creating the appropriate repository instance."""
    
    _instance: Optional[EntryRepository] = None
    
    @classmethod
    def get_repository(cls) -> EntryRepository:
        """Get the configured repository instance."""
        if cls._instance is None:
            cls._instance = cls._create_repository()
        return cls._instance
    
    @classmethod
    def _create_repository(cls) -> EntryRepository:
        """Create the appropriate repository based on configuration."""
        # For now, always return SQLite repository
        # Later this can check environment variables or config files
        # to choose between SQLiteRepository, HTTPRepository, etc.
        
        repo_type = os.environ.get("TJ_REPOSITORY_TYPE", "sqlite")
        
        if repo_type == "sqlite":
            return SQLiteRepository()
        elif repo_type == "http":
            # Future: return HTTPRepository(base_url=...)
            raise NotImplementedError("HTTP repository not yet implemented")
        elif repo_type == "hybrid":
            # Future: return CachingRepository(remote=..., local=...)
            raise NotImplementedError("Hybrid repository not yet implemented")
        else:
            raise ValueError(f"Unknown repository type: {repo_type}")
    
    @classmethod
    def reset(cls):
        """Reset the factory (mainly for testing)."""
        cls._instance = None