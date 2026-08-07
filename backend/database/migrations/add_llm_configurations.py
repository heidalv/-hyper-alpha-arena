#!/usr/bin/env python3
"""
Migration: Add unified LLM configurations table and link to accounts

Creates:
- llm_configurations table for centralized LLM config management
- Adds llm_config_id foreign key to accounts table
- Migrates existing account LLM configs to the new table

This migration is idempotent - safe to run multiple times.
"""
import os
import sys
import logging
from datetime import datetime

# Add backend to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.database.connection import get_db
from sqlalchemy import text

logger = logging.getLogger(__name__)


def check_table_exists(db, table_name: str) -> bool:
    """Check if a table exists in the database."""
    result = db.execute(text(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :table_name)"
    ), {"table_name": table_name})
    return result.scalar()


def check_column_exists(db, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    result = db.execute(text(
        "SELECT EXISTS (SELECT FROM information_schema.columns WHERE table_name = :table_name AND column_name = :column_name)"
    ), {"table_name": table_name, "column_name": column_name})
    return result.scalar()


def upgrade():
    """Run the migration."""
    db = next(get_db())
    
    try:
        # Step 1: Create llm_configurations table if not exists
        if not check_table_exists(db, "llm_configurations"):
            logger.info("Creating llm_configurations table...")
            db.execute(text("""
                CREATE TABLE llm_configurations (
                    id SERIAL PRIMARY KEY,
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
            """))
            db.commit()
            logger.info("✓ Created llm_configurations table")
            
            # Create index on provider
            db.execute(text("CREATE INDEX IF NOT EXISTS idx_llm_configs_provider ON llm_configurations(provider)"))
            db.execute(text("CREATE INDEX IF NOT EXISTS idx_llm_configs_is_active ON llm_configurations(is_active)"))
            db.commit()
            logger.info("✓ Created indexes on llm_configurations")
        else:
            logger.info("llm_configurations table already exists, skipping creation")
        
        # Step 2: Add llm_config_id column to accounts if not exists
        if not check_column_exists(db, "accounts", "llm_config_id"):
            logger.info("Adding llm_config_id column to accounts table...")
            db.execute(text("""
                ALTER TABLE accounts 
                ADD COLUMN llm_config_id INTEGER REFERENCES llm_configurations(id)
            """))
            db.commit()
            logger.info("✓ Added llm_config_id column to accounts table")
        else:
            logger.info("llm_config_id column already exists in accounts, skipping")
        
        # Step 3: Migrate existing account configurations to llm_configurations
        # Find accounts with model/base_url/api_key but no llm_config_id
        logger.info("Checking for existing account LLM configurations to migrate...")
        
        result = db.execute(text("""
            SELECT DISTINCT model, base_url, api_key
            FROM accounts
            WHERE model IS NOT NULL 
              AND base_url IS NOT NULL 
              AND api_key IS NOT NULL
              AND api_key != ''
              AND api_key != 'default-key-please-update-in-settings'
              AND NOT EXISTS (
                  SELECT 1 FROM llm_configurations lc 
                  WHERE lc.model = accounts.model 
                    AND lc.base_url = accounts.base_url 
                    AND lc.api_key = accounts.api_key
              )
        """))
        
        configs_to_migrate = result.fetchall()
        
        if configs_to_migrate:
            logger.info(f"Found {len(configs_to_migrate)} unique LLM configurations to migrate")
            
            for config in configs_to_migrate:
                model, base_url, api_key = config
                
                # Determine provider from base_url
                provider = "custom"
                if "openai.com" in base_url.lower():
                    provider = "openai"
                elif "deepseek.com" in base_url.lower():
                    provider = "deepseek"
                elif "dashscope.aliyuncs.com" in base_url.lower():
                    provider = "qwen"
                elif "volces.com" in base_url.lower():
                    provider = "volcengine"
                
                # Create a name based on provider and model
                config_name = f"{provider.title()} {model}"
                
                # Insert into llm_configurations
                result = db.execute(text("""
                    INSERT INTO llm_configurations (name, provider, model, base_url, api_key, is_active, test_status)
                    VALUES (:name, :provider, :model, :base_url, :api_key, 'true', 'pending')
                    RETURNING id
                """), {
                    "name": config_name,
                    "provider": provider,
                    "model": model,
                    "base_url": base_url,
                    "api_key": api_key
                })
                new_config_id = result.scalar()
                db.commit()
                
                # Update accounts to use this config
                db.execute(text("""
                    UPDATE accounts 
                    SET llm_config_id = :config_id
                    WHERE model = :model 
                      AND base_url = :base_url 
                      AND api_key = :api_key
                      AND llm_config_id IS NULL
                """), {
                    "config_id": new_config_id,
                    "model": model,
                    "base_url": base_url,
                    "api_key": api_key
                })
                db.commit()
                
                logger.info(f"  ✓ Migrated config: {config_name} (id={new_config_id})")
        else:
            logger.info("No existing LLM configurations need migration")
        
        # Step 4: Insert default presets if table is empty
        result = db.execute(text("SELECT COUNT(*) FROM llm_configurations"))
        count = result.scalar()
        
        if count == 0:
            logger.info("Inserting default LLM provider presets...")
            
            # Insert OpenAI preset (as template, no real API key)
            db.execute(text("""
                INSERT INTO llm_configurations (name, provider, description, model, base_url, api_key, is_default, is_active, test_status)
                VALUES 
                    ('OpenAI GPT-4o (模板)', 'openai', 'OpenAI GPT-4o - 强大的通用模型，适合复杂交易分析', 'gpt-4o', 'https://api.openai.com/v1', '', 'true', 'false', 'pending'),
                    ('Deepseek Chat (模板)', 'deepseek', 'Deepseek Chat - 高性价比的国产模型', 'deepseek-chat', 'https://api.deepseek.com', '', 'false', 'false', 'pending'),
                    ('通义千问 Plus (模板)', 'qwen', '阿里云通义千问 - 适合中文场景的交易分析', 'qwen-plus', 'https://dashscope.aliyuncs.com/compatible-mode/v1', '', 'false', 'false', 'pending'),
                    ('火山引擎 (模板)', 'volcengine', '字节跳动火山引擎 - 豆包大模型', 'ep-xxxxxx-xxxxx', 'https://ark.cn-beijing.volces.com/api/v3', '', 'false', 'false', 'pending')
            """))
            db.commit()
            logger.info("✓ Inserted default LLM provider presets")
        
        logger.info("✓ LLM configurations migration completed successfully")
        
    except Exception as e:
        db.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        db.close()


def downgrade():
    """Rollback the migration (for development only)."""
    db = next(get_db())
    
    try:
        # Remove foreign key column first
        if check_column_exists(db, "accounts", "llm_config_id"):
            db.execute(text("ALTER TABLE accounts DROP COLUMN llm_config_id"))
            db.commit()
            logger.info("✓ Removed llm_config_id column from accounts")
        
        # Drop the table
        if check_table_exists(db, "llm_configurations"):
            db.execute(text("DROP TABLE llm_configurations CASCADE"))
            db.commit()
            logger.info("✓ Dropped llm_configurations table")
            
    except Exception as e:
        db.rollback()
        logger.error(f"Downgrade failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade()
