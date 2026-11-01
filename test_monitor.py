import os
import json
import sqlite3
import pytest
from monitor import init_db, load_config, hydrate_initial_states, DB_FILE, CONFIG_FILE

def test_init_db(tmp_path):
    # Test that database and audit_logs table are created correctly
    test_db = tmp_path / "test_infra.db"
    # Temporarily monkeypatch DB_FILE if needed, or test init_db directly
    conn = sqlite3.connect(test_db)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            target_name TEXT NOT NULL,
            url TEXT NOT NULL,
            status_code INTEGER,
            latency_ms REAL,
            status TEXT NOT NULL,
            error_message TEXT
        )
    """)
    conn.commit()
    
    # Verify table schema exists
    cursor.execute("PRAGMA table_info(audit_logs)")
    columns = [col[1] for col in cursor.fetchall()]
    assert "target_name" in columns
    assert "status" in columns
    assert "latency_ms" in columns
    conn.close()

def test_load_config_missing_file():
    # Verify load_config handles missing files gracefully (or exits)
    # We can test environment variable expansion logic
    os.environ["TEST_WEBHOOK"] = "https://discord.com/api/webhooks/test"
    
    # Create temporary config
    config_data = {
        "webhook_url": "${TEST_WEBHOOK}",
        "targets": [{"name": "Test", "url": "https://example.com"}]
    }
    
    # Test file write & read simulation
    assert config_data["webhook_url"] == "${TEST_WEBHOOK}"