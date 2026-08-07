import sqlite3
import os

db_path = 'd:/001Alpha/Hyper-Alpha-Arena/data/alpha_arena.db'
out_path = 'd:/001Alpha/Hyper-Alpha-Arena/backend/fix_result.txt'

with open(out_path, 'w') as f:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        f.write(f"Existing tables: {tables}\n")
        
        # Create llm_configurations table if not exists
        if 'llm_configurations' not in tables:
            f.write("Creating llm_configurations table...\n")
            cursor.execute('''
                CREATE TABLE llm_configurations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL,
                    provider VARCHAR(50) NOT NULL,
                    description VARCHAR(500),
                    model VARCHAR(100) NOT NULL,
                    base_url VARCHAR(500) NOT NULL,
                    api_key VARCHAR(500) NOT NULL,
                    is_default VARCHAR(10) NOT NULL DEFAULT 'false',
                    is_active VARCHAR(10) NOT NULL DEFAULT 'true',
                    last_tested_at TIMESTAMP,
                    test_status VARCHAR(20),
                    test_message VARCHAR(500),
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            f.write("SUCCESS: Created llm_configurations table\n")
        else:
            f.write("Table llm_configurations already exists\n")
        
        # Check if llm_config_id column exists in accounts
        cursor.execute('PRAGMA table_info(accounts)')
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'llm_config_id' not in columns:
            cursor.execute('ALTER TABLE accounts ADD COLUMN llm_config_id INTEGER')
            conn.commit()
            f.write("SUCCESS: Added llm_config_id column to accounts\n")
        else:
            f.write("Column llm_config_id already exists in accounts\n")
        
        conn.close()
        f.write("\nAll migrations completed successfully!\n")
    except Exception as e:
        f.write(f"ERROR: {e}\n")
