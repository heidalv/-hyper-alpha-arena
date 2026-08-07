#!/usr/bin/env python3
"""Phase 0 验收：paper_positions 按 tier 统计。"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import psycopg

url = os.getenv("DATABASE_URL", "").replace("+psycopg", "")
conn = psycopg.connect(url)
cur = conn.cursor()
cur.execute(
    """
    SELECT timeframe_tier, COUNT(*), MAX(opened_at)
    FROM paper_positions
    WHERE opened_at > NOW() - INTERVAL '3 days'
    GROUP BY timeframe_tier
    ORDER BY timeframe_tier
    """
)
print("=== paper_positions (3 days) ===")
for row in cur.fetchall():
    print(row)
conn.close()
