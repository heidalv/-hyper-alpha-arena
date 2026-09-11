# -*- coding: utf-8 -*-
"""Z204：核对 `paper_positions` 的真实列名（我前两个脚本用错了列，SQL 直接报错）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from sqlalchemy import text as t  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

db = SessionLocal()
db.execute(t("set app.is_admin='on'"))
try:
    cols = [
        r[0] for r in db.execute(t(
            "select column_name from information_schema.columns "
            "where table_name='paper_positions' order by ordinal_position"
        )).fetchall()
    ]
    for key in ("fee", "realized", "pnl", "close", "peak", "trough"):
        print(f"含 {key!r} 的列: {[c for c in cols if key in c]}")
    print("\n全部列数:", len(cols))
finally:
    db.rollback()
    db.close()
