# -*- coding: utf-8 -*-
"""Z176（P12/P13 生效核验）：确认组合闸的输入口径与每标的并发上限已在**运行进程**里生效。

做法：
  1. 打印两个开关的**运行期实际值**（settings / env）；
  2. 用真实持仓快照（DB）复算一次组合闸：分别用「旧口径名义」与「同口径名义」跑，
     展示判据差异（这正是 P12 的物理效果）；
  3. 复算每标的同向并发计数，确认 P13 阈值（默认 2）与当前持仓的关系。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from backend.config import settings  # noqa: E402
from backend.services.full_auto.midlong_helpers import _cfg_bool_env  # noqa: E402
from backend.services.mlto.midlong_portfolio_risk import (  # noqa: E402
    check_portfolio_open_allowed,
    collect_midlong_positions,
    estimate_open_notional,
    estimate_open_notional_aligned,
)

print("=== 1. 两个开关的运行期实际值 ===")
print("  MIDLONG_MAX_SAME_SYMBOL_POSITIONS =", getattr(settings, "MIDLONG_MAX_SAME_SYMBOL_POSITIONS", "<无>"))
print("  MIDLONG_MAX_OPEN_POSITIONS        =", getattr(settings, "MIDLONG_MAX_OPEN_POSITIONS", "<无>"))
print("  MIDLONG_MAX_NET_EXPOSURE_PCT      =", getattr(settings, "MIDLONG_MAX_NET_EXPOSURE_PCT", "<无>"))
print("  MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED=", _cfg_bool_env("MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED", True),
      f"(env={os.getenv('MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED')!r})")

print("\n=== 2. 真实持仓 + 两种口径下的组合闸判据 ===")
try:
    from sqlalchemy import text as _t

    from backend.database.connection import SessionLocal

    db = SessionLocal()
    db.execute(_t("set app.is_admin='on'"))
    rows = db.execute(_t(
        "select symbol, side, size, entry_price, mark_price, margin, trade_nature, timeframe_tier, "
        "account_id from paper_positions where status='open' order by id desc limit 50"
    )).fetchall()
    db.rollback()
    positions = [
        {"symbol": r[0], "side": r[1], "size": float(r[2] or 0), "entry_price": float(r[3] or 0),
         "mark_price": float(r[4] or 0), "margin": float(r[5] or 0),
         "trade_nature": r[6], "timeframe_tier": r[7], "account_id": r[8]}
        for r in rows
    ]
    mids = collect_midlong_positions(None, positions)
    equity = float(os.getenv("_EQ_PROBE") or 0) or 4717.0
    print(f"  开仓中 mid/long 持仓: {len(mids)} 笔 -> "
          f"{[(p.get('symbol'), p.get('side')) for p in mids]}")
    pf = {"balance": {"total_equity": equity}, "positions": positions}

    sl, risk = 0.045, 0.0075
    legacy = estimate_open_notional(equity=equity, margin_frac=0.15, leverage=10.0)
    aligned = estimate_open_notional_aligned(equity=equity, sl_pct=sl, risk_pct=risk)
    print(f"  同一次建仓：旧口径 ${legacy:,.0f}（{legacy/equity:.0%} 权益） vs 同口径 ${aligned:,.0f}"
          f"（{aligned/equity:.0%}） 倍差 {legacy/aligned:.1f}x")
    for label, notional in (("旧口径输入", legacy), ("同口径输入(P12 生效)", aligned)):
        ok, why = check_portfolio_open_allowed(
            symbol="SOL", action="buy", portfolio=pf, new_notional=notional,
        )
        print(f"  {label:24s} -> {'放行' if ok else '拦截'}  {why[:90]}")
    # 每标的并发（P13）
    from collections import Counter
    same = Counter(str(p.get("symbol") or "").upper() for p in mids)
    print("  每标的 mid/long 持仓数:", dict(same))
except Exception as exc:  # noqa: BLE001
    print("  持仓探测失败:", type(exc).__name__, str(exc)[:160])
