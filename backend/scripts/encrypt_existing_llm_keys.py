#!/usr/bin/env python3
"""
One-time migration: encrypt existing plaintext LLM API keys.
Run once after deploying encryption changes. Idempotent — safe to re-run.

Usage:
  uv run python scripts/encrypt_existing_llm_keys.py
"""
import sys
import os
import logging

# Ensure backend is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from backend.database.connection import SessionLocal
from backend.database.models import LLMConfiguration
from backend.utils.encryption import is_encrypted, encrypt_llm_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    db = SessionLocal()
    try:
        configs = db.query(LLMConfiguration).all()
        encrypted_count = 0
        skipped_count = 0

        for cfg in configs:
            if not cfg.api_key:
                skipped_count += 1
                continue
            if is_encrypted(cfg.api_key):
                skipped_count += 1
                continue

            plaintext = cfg.api_key
            cfg.api_key = encrypt_llm_key(plaintext)
            encrypted_count += 1
            logger.info(
                "Encrypted: config %d (%s / %s)",
                cfg.id, cfg.name, cfg.provider,
            )

        if encrypted_count > 0:
            db.commit()
            logger.info(
                "Migration complete: %d encrypted, %d skipped (already encrypted or empty)",
                encrypted_count, skipped_count,
            )
        else:
            db.rollback()
            logger.info("No plaintext keys found — all already encrypted or empty")

    except Exception as e:
        db.rollback()
        logger.error("Migration failed: %s", e)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
