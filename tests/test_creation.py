import sys
from tj.cli import entrypoint
from tj.database import get_db_connection

def test_create_memory(isolated_env, monkeypatch):
    """Test creating a basic memory entry with a tag."""
    test_args = ["tj.py", "This is a test memory", "#test"]
    monkeypatch.setattr(sys, 'argv', test_args)
    
    entrypoint()
    
    # Verify entry was created
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT content, kind FROM entries")
    entries = cursor.fetchall()
    assert len(entries) == 1
    
    entry = entries[0]
    assert entry['content'] == "This is a test memory #test"
    assert entry['kind'] == "memory"
    
    # Verify tag was created
    cursor.execute("SELECT tag_name FROM tags")
    tags = cursor.fetchall()
    assert len(tags) == 1
    assert tags[0]['tag_name'] == "test"
    
    conn.close()

def test_create_bookmark(isolated_env, monkeypatch):
    """Test creating a bookmark entry."""
    test_args = ["tj.py", "https://example.com", "#bookmark"]
    monkeypatch.setattr(sys, 'argv', test_args)
    
    entrypoint()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT content, kind FROM entries")
    entries = cursor.fetchall()
    assert len(entries) == 1
    
    entry = entries[0]
    assert entry['content'] == "https://example.com #bookmark"
    assert entry['kind'] == "bookmark"
    
    conn.close()

def test_create_todo(isolated_env, monkeypatch):
    """Test creating a todo entry using 'd' command."""
    test_args = ["tj.py", "d", "finish the project"]
    monkeypatch.setattr(sys, 'argv', test_args)
    
    entrypoint()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT content, kind FROM entries")
    entries = cursor.fetchall()
    assert len(entries) == 1
    
    entry = entries[0]
    assert entry['content'] == "finish the project"
    assert entry['kind'] == "todo"
    
    conn.close()

def test_create_profile_fact(isolated_env, monkeypatch):
    """Test creating a profile entry using 'p' command."""
    test_args = ["tj.py", "p", "I live in San Francisco"]
    monkeypatch.setattr(sys, 'argv', test_args)
    
    entrypoint()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT content, kind FROM entries")
    entries = cursor.fetchall()
    assert len(entries) == 1
    
    entry = entries[0]
    assert entry['content'] == "I live in San Francisco"
    assert entry['kind'] == "profile"
    
    conn.close()

def test_create_calendar_entry(isolated_env, monkeypatch):
    """Test creating a calendar entry with date format."""
    test_args = ["tj.py", "20241225", "Christmas dinner"]
    monkeypatch.setattr(sys, 'argv', test_args)
    
    entrypoint()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT content, kind, data FROM entries")
    entries = cursor.fetchall()
    assert len(entries) == 1
    
    entry = entries[0]
    assert entry['content'] == "20241225 Christmas dinner" 
    assert entry['kind'] == "calendar"
    
    # Check event_date is in the data JSON
    import json
    data = json.loads(entry['data']) if entry['data'] else {}
    assert 'event_date' in data
    
    conn.close()
