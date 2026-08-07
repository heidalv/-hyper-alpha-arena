"""
Migration: Add weights fields to signal_pools table
"""
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)


def upgrade(engine):
    """Add weights and weight_threshold columns to signal_pools table"""
    with engine.connect() as conn:
        try:
            # Check if columns exist
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'signal_pools' 
                AND column_name IN ('weights', 'weight_threshold')
            """))
            existing_columns = [row[0] for row in result.fetchall()]

            # Add weights column if not exists
            if 'weights' not in existing_columns:
                conn.execute(text("""
                    ALTER TABLE signal_pools 
                    ADD COLUMN weights TEXT DEFAULT NULL
                """))
                logger.info("Added 'weights' column to signal_pools table")

            # Add weight_threshold column if not exists
            if 'weight_threshold' not in existing_columns:
                conn.execute(text("""
                    ALTER TABLE signal_pools 
                    ADD COLUMN weight_threshold FLOAT DEFAULT 0.5
                """))
                logger.info("Added 'weight_threshold' column to signal_pools table")

            conn.commit()
            logger.info("Migration completed: add_weights_to_signal_pools")
            return True

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            conn.rollback()
            return False


def downgrade(engine):
    """Remove weights and weight_threshold columns from signal_pools table"""
    with engine.connect() as conn:
        try:
            conn.execute(text("""
                ALTER TABLE signal_pools 
                DROP COLUMN IF EXISTS weights,
                DROP COLUMN IF EXISTS weight_threshold
            """))
            conn.commit()
            logger.info("Downgrade completed: add_weights_to_signal_pools")
            return True
        except Exception as e:
            logger.error(f"Downgrade failed: {e}")
            conn.rollback()
            return False


if __name__ == "__main__":
    # For standalone testing
    import sys
    sys.path.insert(0, '../..')
    from backend.database.connection import engine
    
    logging.basicConfig(level=logging.INFO)
    upgrade(engine)
