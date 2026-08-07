#!/usr/bin/env python3
"""Fix database - add missing llm_config_id column"""
import sys
import os

# Output to file since PowerShell has issues
log_file = open(os.path.join(os.path.dirname(__file__), 'migration_output.txt'), 'w', encoding='utf-8')
def log(msg):
    print(msg)
    log_file.write(msg + '\n')
    log_file.flush()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.database.connection import SessionLocal, engine
from sqlalchemy import text

log(f"Database URL: {engine.url}")
is_sqlite = str(engine.url).startswith('sqlite')
log(f"Is SQLite: {is_sqlite}")

db = SessionLocal()
try:
    if is_sqlite:
        # SQLite: Check if column exists
        result = db.execute(text("PRAGMA table_info(accounts)"))
        columns = [row[1] for row in result.fetchall()]
        
        if 'llm_config_id' not in columns:
            log("Adding llm_config_id column to accounts table...")
            db.execute(text("ALTER TABLE accounts ADD COLUMN llm_config_id INTEGER"))
            db.commit()
            log("Done!")
        else:
            log("Column llm_config_id already exists")
    else:
        # PostgreSQL: Check if column exists
        result = db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'accounts' AND column_name = 'llm_config_id'"
        ))
        if result.fetchone() is None:
            log("Adding llm_config_id column to accounts table (PostgreSQL)...")
            db.execute(text("ALTER TABLE accounts ADD COLUMN llm_config_id INTEGER"))
            db.commit()
            log("Done!")
        else:
            log("Column llm_config_id already exists (PostgreSQL)")
        
except Exception as e:
    log(f"Error: {e}")
    import traceback
    traceback.print_exc(file=log_file)
    db.rollback()
finally:
    db.close()
    log_file.close()
