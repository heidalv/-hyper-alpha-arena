# -*- coding: utf-8 -*-
"""F1 验收：`get_orders` 新旧实现**逐字节等价** + 性能对照（真实库，只读）。

旧实现（对拍基线，内联复刻）：
    orders = limit(50) 查询
    positions = 该账户**全部** paper_positions（ORM 实体）
    for o: d = _order_to_dict(o); if not d.entry_price: 回放推断 or 解析器(o, positions)
"""
from __future__ import annotations

import io
import json
import statistics
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.database.connection import SessionLocal  # noqa: E402
from backend.database.models import PaperOrder, PaperPosition  # noqa: E402
from backend.services.paper_trading_engine import paper_engine  # noqa: E402

ACCOUNTS = [14, 149, 147]
N = 10


def old_get_orders(db, account_id, status=None, limit=50):
    """旧实现原样复刻（对拍基线）。"""
    q = db.query(PaperOrder).filter(PaperOrder.account_id == account_id)
    if status:
        q = q.filter(PaperOrder.status == status)
    orders = q.order_by(PaperOrder.id.desc()).limit(limit).all()
    positions = db.query(PaperPosition).filter(
        PaperPosition.account_id == account_id
    ).all()
    entry_fallback = paper_engine._build_entry_price_fallback(orders)
    result = []
    for o in orders:
        d = paper_engine._order_to_dict(o)
        if not d.get("entry_price"):
            d["entry_price"] = entry_fallback.get(o.id) or paper_engine._resolve_entry_from_positions(o, positions)
        result.append(d)
    return result


print("=" * 100)
print("【1】逐账户对拍：新实现 vs 旧实现（JSON 全等）")
print("=" * 100)
ok_all = True
for acct in ACCOUNTS:
    db = SessionLocal()
    try:
        new = paper_engine.get_orders(db, acct, None, 50)
        old = old_get_orders(db, acct, None, 50)
    finally:
        db.close()
    same = json.dumps(new, sort_keys=True, default=str) == json.dumps(old, sort_keys=True, default=str)
    ok_all &= same
    ne_price = sum(1 for r in new if r.get("entry_price"))
    print(f"  账号 {acct:4d}: 行数 新={len(new):3d} 旧={len(old):3d}  "
          f"有 entry_price={ne_price:3d}  全等={'是 ✓' if same else '否 ✗'}")
    if not same:
        for a, b in zip(new, old):
            if json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str):
                ka = {k: v for k, v in a.items() if a.get(k) != b.get(k)}
                kb = {k: v for k, v in b.items() if a.get(k) != b.get(k)}
                print("    首个差异行 新:", json.dumps(ka, ensure_ascii=False, default=str)[:200])
                print("                旧:", json.dumps(kb, ensure_ascii=False, default=str)[:200])
                break

print()
print("=" * 100)
print("【2】性能对照（每账户 10 次，真实库）")
print("=" * 100)
print(f"  {'账号':>6s} {'旧实现中位':>12s} {'新实现中位':>12s} {'提速':>8s}")
for acct in ACCOUNTS:
    db = SessionLocal()
    try:
        t_old, t_new = [], []
        for _ in range(N):
            t0 = time.perf_counter(); old_get_orders(db, acct, None, 50); t_old.append(time.perf_counter() - t0)
            t0 = time.perf_counter(); paper_engine.get_orders(db, acct, None, 50); t_new.append(time.perf_counter() - t0)
    finally:
        db.close()
    mo, mn = statistics.median(t_old) * 1000, statistics.median(t_new) * 1000
    print(f"  {acct:6d} {mo:11.2f}ms {mn:11.2f}ms {mo/max(mn,0.001):7.1f}×")

print()
print("=" * 100)
print("【3】SQL 指纹：新实现不得再全量物化 ORM 实体")
print("=" * 100)
from sqlalchemy import event  # noqa: E402
from backend.database.connection import engine  # noqa: E402

stmts: list[str] = []


@event.listens_for(engine, "before_cursor_execute")
def _cap(conn, cursor, statement, parameters, context, executemany):
    stmts.append(" ".join(statement.split())[:150])


db = SessionLocal()
stmts.clear()
paper_engine.get_orders(db, 14, None, 50)
db.close()
for s in stmts:
    mark = ""
    if "paper_positions" in s:
        mark = "  ← 含 id 主键列（说明仍在整实体化）" if "paper_positions.id AS" in s else "  ✓ 投影查询"
    print(f"  {s[:140]}{mark}")
print(f"\n  语句数 = {len(stmts)}")

print()
print(f"【总判定】对拍全等 = {'通过 ✓' if ok_all else '失败 ✗'}")
