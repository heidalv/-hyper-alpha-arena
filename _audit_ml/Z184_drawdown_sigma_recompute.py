# -*- coding: utf-8 -*-
"""Z184：复算 `drawdown=18.99σ` —— 熔断输入是否可以自愈（自锁判定）。

按 `portfolio_budget._strategy_drawdown_sigma()` 的**逐行同口径**复算：
    pnls   = 近 PB_DD_LOOKBACK_DAYS 天已平仓 midlong 持仓的 [(close-entry)*size*dir + partial_realized]
    sigma  = std(pnls)                      # 单笔 PnL 标准差（美元）
    curve  = cumsum(pnls)                   # 累计 PnL 曲线
    peak   = max(cummax(curve))
    dd     = peak - curve[-1]
    ratio  = dd / sigma                     # 即日志里的 18.99σ

自锁判定：若"冻结 → 禁止开仓 → 无新平仓 → 曲线头部不变 → ratio 不变"，
则该熔断在亏损期**永远无法解除**（只能靠冷却窗口短暂放行后再次触发）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

import datetime as _dt  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

LOOKBACK = int(os.getenv("PB_DD_LOOKBACK_DAYS", "0") or 0)
print("PB_DD_LOOKBACK_DAYS(env) =", LOOKBACK or "(未设 → 代码默认)")
for k in ("PB_STRATEGY_DRAWDOWN_SIGMA", "PB_STRATEGY_DRAWDOWN_SIGMA_MIDLONG",
          "PB_FREEZE_ENABLED", "PB_FREEZE_COOLDOWN_SEC", "PB_DD_CACHE_TTL_SEC",
          "PB_MIN_TRADES_FOR_CIRCUIT", "PB_ACCOUNT_FREEZE_COOLDOWN_SEC"):
    print(f"  {k} = {os.getenv(k, '(未设)')}")

from backend.database.models import PaperPosition  # noqa: E402
from backend.services.risk_management import portfolio_budget as pbm  # noqa: E402

pb = pbm.portfolio_budget
lookback_days = pbm._cfg_int("PB_DD_LOOKBACK_DAYS", pbm.PB_DD_LOOKBACK_DAYS) if hasattr(pbm, "PB_DD_LOOKBACK_DAYS") else 30
print(f"\n生效 lookback = {lookback_days} 天；min_trades = {pbm._cfg_int('PB_MIN_TRADES_FOR_CIRCUIT', 10)}")

db = SessionLocal()
try:
    cutoff = _dt.datetime.now() - _dt.timedelta(days=lookback_days)
    rows = (
        db.query(PaperPosition)
        .filter(PaperPosition.account_id == 14,
                PaperPosition.status == "closed",
                PaperPosition.closed_at >= cutoff)
        .order_by(PaperPosition.closed_at.asc())
        .all()
    )
    pnls, kept = [], []
    for r in rows:
        if not pbm._is_strategy_pos(
            {"trade_nature": r.trade_nature, "timeframe_tier": r.timeframe_tier}, "midlong"
        ):
            continue
        d = 1 if str(r.side or "").lower() in ("long", "buy") else -1
        if r.close_price is not None and r.entry_price:
            pnl = (float(r.close_price) - float(r.entry_price)) * float(r.size or 0) * d
        else:
            pnl = float(r.unrealized_pnl or 0)
        pnl += float(r.partial_realized_pnl or 0)
        if np.isfinite(pnl):
            pnls.append(pnl)
            kept.append((r.id, r.symbol, str(r.closed_at)[:19], round(pnl, 2)))

    print(f"参与计算的已平仓 midlong 笔数 = {len(pnls)}（窗口 {lookback_days} 天）")
    if len(pnls) >= 2:
        arr = np.asarray(pnls, dtype=float)
        sigma = float(arr.std())
        curve = np.cumsum(arr)
        peak = float(np.maximum.accumulate(curve)[-1])
        dd = float(peak - curve[-1])
        print(f"  sigma(单笔σ) = {sigma:.2f} 美元")
        print(f"  累计曲线末值 = {curve[-1]:.2f} 美元；峰值 = {peak:.2f} 美元")
        print(f"  当前回撤 = {dd:.2f} 美元")
        print(f"  **ratio = dd/σ = {dd / sigma:.2f}σ**（阈值 10σ）")
        print(f"  最近 8 笔（时间/币/单笔PnL）: {kept[-8:]}")

    print("\n=== 自锁判定：近 N 小时内是否有新的已平仓 midlong ===")
    for hours in (1, 3, 6, 12, 24):
        c = (
            db.query(PaperPosition)
            .filter(PaperPosition.account_id == 14,
                    PaperPosition.status == "closed",
                    PaperPosition.closed_at >= _dt.datetime.now() - _dt.timedelta(hours=hours))
            .count()
        )
        c_ml = sum(
            1 for r in db.query(PaperPosition)
            .filter(PaperPosition.account_id == 14,
                    PaperPosition.status == "closed",
                    PaperPosition.closed_at >= _dt.datetime.now() - _dt.timedelta(hours=hours))
            .all()
            if pbm._is_strategy_pos({"trade_nature": r.trade_nature,
                                     "timeframe_tier": r.timeframe_tier}, "midlong")
        )
        print(f"  近 {hours:2d}h: 全策略平仓 {c:4d} 笔 / 其中 midlong {c_ml:3d} 笔")
    open_n = (
        db.query(PaperPosition)
        .filter(PaperPosition.account_id == 14, PaperPosition.status == "open")
        .count()
    )
    print(f"  当前开仓中: {open_n} 笔")
finally:
    db.close()

print("\n=== 冻结表状态 ===")
try:
    st = pb.get_state()
    for k in ("global_frozen", "strategy_frozen", "key_frozen", "account_frozen"):
        v = st.get(k)
        if isinstance(v, dict):
            now = __import__("time").time()
            print(f"  {k}: {len(v)} 条 -> " + ", ".join(
                f"{kk}:{int(vv - now)}s" for kk, vv in list(v.items())[:6]))
        else:
            print(f"  {k}: {v}")
except Exception as exc:  # noqa: BLE001
    print("  get_state 失败:", type(exc).__name__, str(exc)[:160])
