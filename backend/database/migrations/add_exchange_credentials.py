"""
Migration: Create exchange_credentials table

Stores API keys for Binance, Bybit, OKX, Gate.io, Asterdex.
(Hyperliquid keeps its own HyperliquidWallet system.)
"""

import logging
from sqlalchemy import text
from backend.database.dialect import dialect

logger = logging.getLogger(__name__)


def migrate(engine):
    """Create exchange_credentials table if it doesn't exist."""
    pk = dialect.auto_pk()
    ddl = text(f"""
    CREATE TABLE IF NOT EXISTS exchange_credentials (
        id          {pk},
        account_id  INTEGER NOT NULL,
        exchange    VARCHAR(32) NOT NULL,
        label       VARCHAR(100) DEFAULT '',
        api_key_encrypted   TEXT DEFAULT '',
        api_secret_encrypted TEXT DEFAULT '',
        passphrase_encrypted TEXT DEFAULT '',
        testnet     BOOLEAN DEFAULT 1,
        enabled     BOOLEAN DEFAULT 0,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (account_id) REFERENCES accounts(id)
    )
    """)

    idx_account = text(
        "CREATE INDEX IF NOT EXISTS ix_exchange_credentials_account_id "
        "ON exchange_credentials (account_id)"
    )
    idx_exchange = text(
        "CREATE INDEX IF NOT EXISTS ix_exchange_credentials_exchange "
        "ON exchange_credentials (exchange)"
    )

    with engine.begin() as conn:
        conn.execute(ddl)
        conn.execute(idx_account)
        conn.execute(idx_exchange)

    logger.info("[Migration] exchange_credentials table ready")
