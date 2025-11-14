import sys
from tj.cli import entrypoint
from tj.state import get_state

def test_set_context(isolated_env, monkeypatch):
    """Test setting a context."""
    test_args = ["tj.py", "c", "work"]
    monkeypatch.setattr(sys, 'argv', test_args)
    
    entrypoint()
    
    state = get_state()
    assert state['current_context'] == "work"

def test_clear_context(isolated_env, monkeypatch):
    """Test clearing context."""
    # First set a context
    test_args = ["tj.py", "c", "work"] 
    monkeypatch.setattr(sys, 'argv', test_args)
    entrypoint()
    
    # Then clear it
    test_args = ["tj.py", "c"]
    monkeypatch.setattr(sys, 'argv', test_args)
    entrypoint()
    
    state = get_state()
    assert state['current_context'] is None

def test_entry_inherits_context(isolated_env, monkeypatch):
    """Test that entries inherit the current context."""
    # Set context
    test_args = ["tj.py", "c", "project-x"]
    monkeypatch.setattr(sys, 'argv', test_args)
    entrypoint()
    
    # Create entry
    test_args = ["tj.py", "working on feature"]
    monkeypatch.setattr(sys, 'argv', test_args)
    entrypoint()
    
    # Verify entry has context
    from tj.database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT context FROM entries")
    entries = cursor.fetchall()
    assert len(entries) == 1
    assert entries[0]['context'] == "project-x"
    
    conn.close()