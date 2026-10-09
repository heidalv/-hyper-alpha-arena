# -*- coding: utf-8 -*-
"""锚定影响量级：账号 14 的 paper_positions / paper_orders 真实行数，
以及各只读端点「实际扫了多少行」对照。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402
from backend.database.models import PaperOrder, PaperPosition  # noqa: E402

db = SessionLocal()
print("=" * 92)
print("【1】账号 14 的行数（RLS 已按请求身份放行 admin）")
print("=" * 92)
q = [
    ("paper_positions 全部", "SELECT count(*) FROM paper_positions WHERE account_id=:a"),
    ("paper_positions open", "SELECT count(*) FROM paper_positions WHERE account_id=:a AND status='open'"),
    ("paper_positions closed", "SELECT count(*) FROM paper_positions WHERE account_id=:a AND status<>'open'"),
    ("paper_orders 全部", "SELECT count(*) FROM paper_orders WHERE account_id=:a"),
]
for label, sql in q:
    n = db.execute(text(sql), {"a": 14}).scalar()
    print(f"  {label:26s} {n:>8,}")

print()
print("=" * 92)
print("【2】全表规模（判断这是否只是 14 号账户的问题）")
print("=" * 92)
for label, sql in [
    ("paper_positions 全库", "SELECT count(*) FROM paper_positions"),
    ("paper_orders 全库", "SELECT count(*) FROM paper_orders"),
    ("paper_positions top5 账户",
     "SELECT account_id, count(*) FROM paper_positions GROUP BY 1 ORDER BY 2 DESC LIMIT 5"),
]:
    rows = db.execute(text(sql)).fetchall()
    print(f"  {label}: {rows}")

print()
print("=" * 92)
print("【3】ORM 物化成本实测：全量 ORM vs 只取 3 列 vs load_only")
print("=" * 92)
import time  # noqa: E402


def bench(label, fn, n=10):
    fn()  # warm
    t0 = time.perf_counter()
    for _ in range(n):
        r = fn()
    dt = (time.perf_counter() - t0) / n * 1000
    print(f"  {label:52s} {dt:8.2f}ms/次")
    return r


bench("全量 ORM 实体 .all()（现状）",
      lambda: db.query(PaperPosition).filter(PaperPosition.account_id == 14).all())
bench("只取 7 列 .all()（无实体构造）",
      lambda: db.query(
          PaperPosition.id, PaperPosition.symbol, PaperPosition.side,
          PaperPosition.strategy_id, PaperPosition.entry_price,
          PaperPosition.opened_at, PaperPosition.closed_at,
      ).filter(PaperPosition.account_id == 14).all())
bench("只取 open 状态的 ORM 实体",
      lambda: db.query(PaperPosition).filter(
          PaperPosition.account_id == 14, PaperPosition.status == "open").all())
bench("只取 open 状态 7 列",
      lambda: db.query(
          PaperPosition.id, PaperPosition.symbol, PaperPosition.side,
          PaperPosition.strategy_id, PaperPosition.entry_price,
          PaperPosition.opened_at, PaperPosition.closed_at,
      ).filter(PaperPosition.account_id == 14, PaperPosition.status == "open").all())
db.close()
