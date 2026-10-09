# -*- coding: utf-8 -*-
"""F387 取证：`_reflexion_memory` 的 `exit` 字段为何恒为空串。只读（SELECT only）。

假设：`.mappings().all()` 返回 `RowMapping`，而代码写的是
`... if isinstance(r, dict) else ""` ⇒ 该三元**永远走 else** ⇒ `exit` 结构性恒为 ""。

本脚本用**同样的查询**实测两件事：
  ① `isinstance(r, dict)` 到底是不是 False（结构性证据）；
  ② `close_reason` 在库里**有没有值**（决定这是"致命失真"还是"潜在缺陷"）。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("=" * 78)
print("F387 取证：reflexion_memory.exit 恒空（只读）")
print("=" * 78)

try:
    from sqlalchemy import text
    from backend.database.connection import SessionLocal
except Exception as exc:  # noqa: BLE001
    print(f"无法导入 DB 层: {exc}")
    raise SystemExit(2)

db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    rows = db.execute(
        text("""
            SELECT side, entry_price, close_price, size, partial_realized_pnl,
                   closed_at, close_reason
            FROM paper_positions
            WHERE symbol = :sym AND status = 'closed'
              AND closed_at >= now() - make_interval(days => :d)
            ORDER BY closed_at DESC
            LIMIT 12
        """),
        {"sym": "BTC", "d": 14},
    ).mappings().all()

    print(f"\n[1] 行数 = {len(rows)}")
    if rows:
        r0 = rows[0]
        print(f"    类型 = {type(r0).__module__}.{type(r0).__name__}")
        print(f"    isinstance(r, dict)      = {isinstance(r0, dict)}   <-- 代码里的判断")
        import collections.abc as abc
        print(f"    isinstance(r, Mapping)   = {isinstance(r0, abc.Mapping)}")
        print(f"    r.get('close_reason') 可用 = {r0.get('close_reason')!r}")

    print("\n[2] close_reason 实际分布（决定缺陷影响面）")
    non_empty = [str(r.get("close_reason") or "") for r in rows]
    n_ne = [x for x in non_empty if x.strip()]
    print(f"    非空值 = {len(n_ne)}/{len(rows)}")
    for x in dict.fromkeys(non_empty):
        n = non_empty.count(x)
        print(f"      {x!r} x{n}")

    print("\n[3] 结论")
    if rows and not isinstance(rows[0], dict):
        print("    结构性证据成立：isinstance(r, dict) 为 False ⇒ exit 恒为空串（无论库里有没有值）")
    if n_ne:
        print(f"    影响面证据：库里有 {len(n_ne)} 条非空 close_reason 被**丢弃** ⇒ 属**真实信息丢失**")
    else:
        print("    影响面证据：库里 close_reason 亦为空 ⇒ 当前是**潜在缺陷**（一旦上游填值即失真）")
finally:
    db.close()
