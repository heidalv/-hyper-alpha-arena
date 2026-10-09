# -*- coding: utf-8 -*-
"""先看真实 schema，再查。只读。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402

db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    print("[1] 当前库 / 用户")
    r = db.execute(text("SELECT current_database(), current_user")).fetchone()
    print(f"    db={r[0]}  user={r[1]}")

    print("\n[2] paper_positions 全部列")
    rows = db.execute(text("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_name='paper_positions' ORDER BY ordinal_position
    """)).fetchall()
    for c, t in rows:
        print(f"    {c:34s} {t}")

    print("\n[3] 哪些库里也有 paper_positions")
    rows = db.execute(text("""
        SELECT table_catalog, table_schema FROM information_schema.tables
        WHERE table_name='paper_positions'
    """)).fetchall()
    for c, s in rows:
        print(f"    {c}.{s}")
finally:
    db.close()
