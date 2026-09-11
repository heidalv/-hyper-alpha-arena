# -*- coding: utf-8 -*-
"""Z197：连亏熔断（`_consecutive_losses`）的自锁评估 + 现状量化。

口径（与代码逐行一致）：对每个 (account=14, strategy=midlong, symbol) 取最近 10 笔
已平仓（仅按 closed_at desc 排序、**无时间窗**），从最新往前数"连续亏损"笔数；
`≥ PB_CONSEC_LOSS_LIMIT`（默认 5）即触发——触发后该 symbol 的开仓被拒，
而计数只能靠"该 symbol 出现一笔盈利平仓"才能清零 ⇒ **同类自锁**。
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from backend.database.models import PaperPosition  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.risk_management import portfolio_budget as pbm  # noqa: E402

CAP = pbm._cfg_int("PB_CONSEC_LOSS_LIMIT", pbm.PB_CONSEC_LOSS_LIMIT)
print(f"PB_CONSEC_LOSS_LIMIT = {CAP}")
print(f"PB_FREEZE_ENABLED    = {pbm._cfg_bool('PB_FREEZE_ENABLED', True)}")

db = SessionLocal()
try:
    mpr = pbm.portfolio_budget
    syms = [
        r[0] for r in db.query(PaperPosition.symbol)
        .filter(PaperPosition.account_id == 14, PaperPosition.status == "closed")
        .distinct().all()
    ]
    print(f"\n账户 14 有已平仓记录的 symbol 数 = {len(syms)}")
    rows = []
    for s in syms:
        n = mpr._consecutive_losses(db, 14, "midlong", s)
        # 该 symbol 最近一笔平仓时间（判断样本是否陈旧）
        last = (
            db.query(PaperPosition.closed_at)
            .filter(PaperPosition.account_id == 14, PaperPosition.status == "closed",
                    PaperPosition.symbol == s, PaperPosition.closed_at.isnot(None))
            .order_by(PaperPosition.closed_at.desc()).first()
        )
        age_h = None
        if last and last[0] is not None:
            import datetime as _dt
            _now = _dt.datetime.now(tz=getattr(last[0], "tzinfo", None))
            age_h = ( _now - last[0]).total_seconds() / 3600.0
        if n:
            rows.append((s, n, age_h, str(last[0])[:19] if last and last[0] else None))
    rows.sort(key=lambda x: -(x[1] or 0))
    print(f"\n{'symbol':8s} {'连亏':>5s} {'最后平仓距今(h)':>14s}  最后平仓时间")
    hits = 0
    for s, n, age, ts in rows:
        flag = "  ← 触发" if (n or 0) >= CAP else ""
        if (n or 0) >= CAP:
            hits += 1
        print(f"{s:8s} {n:>5d} {('%.1f' % age) if age is not None else '?':>14s}  {ts}{flag}")
    print(f"\n触发连亏熔断的 symbol = {hits} / {len(syms)}")

    print("\n=== 近 6h 审计流里的连亏拦截 ===")
    from backend.services.mlto.midlong_direction_audit import audit_paths, _iter_rows
    since = time.time() - 6 * 3600
    c: Counter[str] = Counter()
    for r in _iter_rows(audit_paths()):
        if float(r.get("epoch") or 0) < since:
            continue
        reason = str(r.get("reason") or "")
        if "连续亏损" in reason or "consec_loss" in reason:
            c[reason[:120]] += 1
    print("  命中:", dict(c) or "（无）")
finally:
    db.rollback()
    db.close()
