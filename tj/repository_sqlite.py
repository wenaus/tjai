"""SQLite implementation of the repository interface."""

import json
import sqlite3
from datetime import datetime
from typing import List, Optional, Dict, Any

from tj.database import get_db_connection, DatabaseError
from tj.repository import EntryRepository, Entry, Tag


class SQLiteRepository(EntryRepository):
    """SQLite implementation of the entry repository."""
    
    def create_entry(self, entry: Entry) -> str:
        """Create a new entry and return its ID."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO entries (id, parent_id, content, kind, timestamp_created,
                                   timestamp_modified, context, is_dirty, name, priority, status, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.id, entry.parent_id, entry.content, entry.kind,
                entry.timestamp_created, entry.timestamp_modified,
                entry.context, 1 if entry.is_dirty else 0,
                entry.name, entry.priority, entry.status,
                json.dumps(entry.data) if entry.data else None
            ))

            conn.commit()
            conn.close()
            return entry.id

        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to create entry: {e}")
    
    def get_entry(self, entry_id: str) -> Optional[Entry]:
        """Get a single entry by ID."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, parent_id, content, kind, timestamp_created,
                       timestamp_modified, context, is_dirty, name, priority, status, data
                FROM entries WHERE id = ?
            """, (entry_id,))

            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            return Entry(
                id=row['id'],
                parent_id=row['parent_id'],
                content=row['content'],
                kind=row['kind'],
                timestamp_created=row['timestamp_created'],
                timestamp_modified=row['timestamp_modified'],
                context=row['context'],
                is_dirty=bool(row['is_dirty']),
                name=row['name'],
                priority=row['priority'],
                status=row['status'],
                data=json.loads(row['data']) if row['data'] else None
            )
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to get entry: {e}")

    def get_entries_by_name(self, name: str) -> List[Entry]:
        """Get all entries with a given name across all contexts.

        Returns a list of entries (may be empty).
        """
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, parent_id, content, kind, timestamp_created,
                       timestamp_modified, context, is_dirty, name, priority, status, data
                FROM entries
                WHERE name = ? AND deleted_at IS NULL
                ORDER BY timestamp_modified DESC
            """, (name,))

            rows = cursor.fetchall()
            conn.close()

            entries = []
            for row in rows:
                entries.append(Entry(
                    id=row['id'],
                    parent_id=row['parent_id'],
                    content=row['content'],
                    kind=row['kind'],
                    timestamp_created=row['timestamp_created'],
                    timestamp_modified=row['timestamp_modified'],
                    context=row['context'],
                    is_dirty=bool(row['is_dirty']),
                    name=row['name'],
                    priority=row['priority'],
                    status=row['status'],
                    data=json.loads(row['data']) if row['data'] else None
                ))

            return entries

        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to get entries by name: {e}")

    def get_entry_by_name(self, name: str, context: Optional[str] = None) -> Optional[Entry]:
        """Get entry by name, optionally within a context.

        If context is None, searches globally across all contexts.
        """
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            if context is None:
                # Search globally, ignoring context
                cursor.execute("""
                    SELECT id, parent_id, content, kind, timestamp_created,
                           timestamp_modified, context, is_dirty, name, priority, status, data
                    FROM entries
                    WHERE name = ? AND deleted_at IS NULL
                """, (name,))
            else:
                # Search within specific context
                cursor.execute("""
                    SELECT id, parent_id, content, kind, timestamp_created,
                           timestamp_modified, context, is_dirty, name, priority, status, data
                    FROM entries
                    WHERE name = ? AND context IS ? AND deleted_at IS NULL
                """, (name, context))

            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            return Entry(
                id=row['id'],
                parent_id=row['parent_id'],
                content=row['content'],
                kind=row['kind'],
                timestamp_created=row['timestamp_created'],
                timestamp_modified=row['timestamp_modified'],
                context=row['context'],
                is_dirty=bool(row['is_dirty']),
                name=row['name'],
                priority=row['priority'],
                status=row['status'],
                data=json.loads(row['data']) if row['data'] else None
            )

        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to get entry by name: {e}")

    def update_entry(self, entry_id: str, **changes) -> bool:
        """Update an entry with the given changes."""
        try:
            if not changes:
                return False
                
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Build dynamic UPDATE query
            set_clauses = []
            values = []
            
            for field, value in changes.items():
                if field == 'data' and isinstance(value, dict):
                    set_clauses.append("data = ?")
                    values.append(json.dumps(value))
                elif field in ['content', 'kind', 'context', 'parent_id', 'name', 'status']:
                    set_clauses.append(f"{field} = ?")
                    values.append(value)
                elif field == 'priority':
                    set_clauses.append("priority = ?")
                    values.append(value)
                elif field == 'is_dirty':
                    set_clauses.append("is_dirty = ?")
                    values.append(1 if value else 0)
            
            if not set_clauses:
                return False
                
            # Always update timestamp and mark dirty
            set_clauses.append("timestamp_modified = ?")
            set_clauses.append("is_dirty = ?")
            values.extend([datetime.now().timestamp(), 1])
            values.append(entry_id)
            
            query = f"UPDATE entries SET {', '.join(set_clauses)} WHERE id = ?"
            cursor.execute(query, values)
            
            success = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return success
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to update entry: {e}")
    
    def delete_entry(self, entry_id: str) -> bool:
        """Delete an entry (soft delete)."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Soft delete by setting deleted_at timestamp
            from datetime import datetime
            cursor.execute("""
                UPDATE entries SET deleted_at = ?, is_dirty = 1 
                WHERE id = ? AND deleted_at IS NULL
            """, (datetime.now().timestamp(), entry_id))
            
            success = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return success
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete entry: {e}")
    
    def query_entries(self,
                     kind: Optional[str] = None,
                     context: Optional[str] = None,
                     tag: Optional[str] = None,
                     priority: Optional[int] = None,
                     status: Optional[str] = None,
                     limit: Optional[int] = None) -> List[Entry]:
        """Query entries with optional filters."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            # Build dynamic query
            conditions = ["deleted_at IS NULL"]  # Exclude soft-deleted entries
            params = []

            if kind:
                conditions.append("kind = ?")
                params.append(kind)

            if context:
                conditions.append("context = ?")
                params.append(context)

            if tag:
                conditions.append("id IN (SELECT entry_id FROM tags WHERE tag_name = ?)")
                params.append(tag)

            if priority is not None:
                conditions.append("priority = ?")
                params.append(priority)

            if status:
                conditions.append("status = ?")
                params.append(status)
            
            where_clause = " AND ".join(conditions)
            query = f"""
                SELECT id, parent_id, content, kind, timestamp_created,
                       timestamp_modified, context, is_dirty, name, priority, status, data
                FROM entries
                WHERE {where_clause}
                ORDER BY timestamp_created DESC
            """

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            return [
                Entry(
                    id=row['id'],
                    parent_id=row['parent_id'],
                    content=row['content'],
                    kind=row['kind'],
                    timestamp_created=row['timestamp_created'],
                    timestamp_modified=row['timestamp_modified'],
                    context=row['context'],
                    is_dirty=bool(row['is_dirty']),
                    name=row['name'],
                    priority=row['priority'],
                    status=row['status'],
                    data=json.loads(row['data']) if row['data'] else None
                ) for row in rows
            ]
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to query entries: {e}")
    
    def add_tag(self, entry_id: str, tag_name: str) -> None:
        """Add a tag to an entry."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR IGNORE INTO tags (tag_name, entry_id) VALUES (?, ?)
            """, (tag_name, entry_id))
            
            conn.commit()
            conn.close()
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to add tag: {e}")
    
    def get_tags(self, entry_id: str) -> List[str]:
        """Get all tags for an entry."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT tag_name FROM tags WHERE entry_id = ?
            """, (entry_id,))
            
            rows = cursor.fetchall()
            conn.close()
            
            return [row['tag_name'] for row in rows]
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to get tags: {e}")
    
    def get_all_tags(self) -> List[Tag]:
        """Get all tags in the system."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT DISTINCT tag_name, entry_id FROM tags
                ORDER BY tag_name
            """)
            
            rows = cursor.fetchall()
            conn.close()
            
            return [Tag(tag_name=row['tag_name'], entry_id=row['entry_id']) for row in rows]
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to get all tags: {e}")
    
    def get_contexts(self) -> List[str]:
        """Get all contexts that have been used."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT DISTINCT context FROM entries 
                WHERE context IS NOT NULL AND deleted_at IS NULL
                ORDER BY context
            """)
            
            rows = cursor.fetchall()
            conn.close()
            
            return [row['context'] for row in rows]
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to get contexts: {e}")
    
    def create_context(self, context: 'Context') -> bool:
        """Create a new context entity."""
        # Validate context name - reject names containing '='
        if '=' in context.name:
            raise DatabaseError(f"Invalid context name '{context.name}': context names cannot contain '='")

        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO contexts
                (name, title, description, timestamp_created, timestamp_modified)
                VALUES (?, ?, ?, ?, ?)
            """, (context.name, context.title, context.description, context.timestamp_created, context.timestamp_modified))

            conn.commit()
            conn.close()
            return True

        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to create context: {e}")
    
    def get_context(self, name: str) -> Optional['Context']:
        """Get a context by name."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT name, title, description, timestamp_created, timestamp_modified
                FROM contexts WHERE name = ?
            """, (name,))

            row = cursor.fetchone()
            conn.close()

            if row:
                from tj.repository import Context
                return Context(
                    name=row['name'],
                    title=row['title'],
                    description=row['description'],
                    timestamp_created=row['timestamp_created'],
                    timestamp_modified=row['timestamp_modified']
                )
            return None

        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to get context: {e}")
    
    def get_all_contexts(self) -> List['Context']:
        """Get all context entities."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT name, title, description, timestamp_created, timestamp_modified
                FROM contexts ORDER BY timestamp_created DESC
            """)

            rows = cursor.fetchall()
            conn.close()

            from tj.repository import Context
            return [Context(
                name=row['name'],
                title=row['title'],
                description=row['description'],
                timestamp_created=row['timestamp_created'],
                timestamp_modified=row['timestamp_modified']
            ) for row in rows]

        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to get all contexts: {e}")
    
    def update_context(self, name: str, **changes) -> bool:
        """Update a context with the given changes."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Build dynamic update query
            set_clauses = []
            params = []
            
            for key, value in changes.items():
                if key in ['description', 'timestamp_modified']:
                    set_clauses.append(f"{key} = ?")
                    params.append(value)
            
            if not set_clauses:
                return False
                
            params.append(name)
            query = f"UPDATE contexts SET {', '.join(set_clauses)} WHERE name = ?"
            
            cursor.execute(query, params)
            success = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return success
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to update context: {e}")
    
    def remove_tag(self, entry_id: str, tag_name: str) -> bool:
        """Remove a specific tag from an entry."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM tags WHERE entry_id = ? AND tag_name = ?
            """, (entry_id, tag_name))
            
            success = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return success
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to remove tag: {e}")
    
    def remove_all_tag_instances(self, tag_name: str) -> int:
        """Remove all instances of a tag from the system. Returns count removed."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM tags WHERE tag_name = ?
            """, (tag_name,))
            
            count_removed = cursor.rowcount
            conn.commit()
            conn.close()
            return count_removed
            
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to remove all tag instances: {e}")