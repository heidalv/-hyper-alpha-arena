"""
Add Binance configuration columns to accounts table
"""
from sqlalchemy import text
from sqlalchemy.orm import Session
from backend.database.connection import SessionLocal

def upgrade():
    """Add Binance columns to accounts table"""
    db: Session = SessionLocal()
    try:
        print("[Migration] Adding Binance columns to accounts table...")
        
        # Check and add binance_enabled column
        db.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='accounts' AND column_name='binance_enabled'
                ) THEN
                    ALTER TABLE accounts ADD COLUMN binance_enabled VARCHAR(10) NOT NULL DEFAULT 'false';
                    RAISE NOTICE 'Added binance_enabled column';
                ELSE
                    RAISE NOTICE 'binance_enabled column already exists';
                END IF;
            END $$;
        """))
        
        # Check and add binance_market_type column
        db.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='accounts' AND column_name='binance_market_type'
                ) THEN
                    ALTER TABLE accounts ADD COLUMN binance_market_type VARCHAR(20) NULL;
                    RAISE NOTICE 'Added binance_market_type column';
                ELSE
                    RAISE NOTICE 'binance_market_type column already exists';
                END IF;
            END $$;
        """))
        
        # Check and add binance_testnet column
        db.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='accounts' AND column_name='binance_testnet'
                ) THEN
                    ALTER TABLE accounts ADD COLUMN binance_testnet VARCHAR(10) NOT NULL DEFAULT 'false';
                    RAISE NOTICE 'Added binance_testnet column';
                ELSE
                    RAISE NOTICE 'binance_testnet column already exists';
                END IF;
            END $$;
        """))
        
        # Check and add binance_api_credentials column
        db.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='accounts' AND column_name='binance_api_credentials'
                ) THEN
                    ALTER TABLE accounts ADD COLUMN binance_api_credentials VARCHAR(1000) NULL;
                    RAISE NOTICE 'Added binance_api_credentials column';
                ELSE
                    RAISE NOTICE 'binance_api_credentials column already exists';
                END IF;
            END $$;
        """))
        
        # Check and add binance_max_leverage column
        db.execute(text("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='accounts' AND column_name='binance_max_leverage'
                ) THEN
                    ALTER TABLE accounts ADD COLUMN binance_max_leverage INTEGER NULL DEFAULT 20;
                    RAISE NOTICE 'Added binance_max_leverage column';
                ELSE
                    RAISE NOTICE 'binance_max_leverage column already exists';
                END IF;
            END $$;
        """))
        
        db.commit()
        print("[Migration] Binance columns migration completed successfully")
        
    except Exception as e:
        db.rollback()
        print(f"[Migration] Failed to add Binance columns: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    upgrade()
