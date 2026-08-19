"""macro_tailwind — 宏观对币市的顺风/逆风三元态（设计总方案 B3，2026-08-19）。

不求预测宏观，只求「当前宏观对 BTC 是顺风/逆风」：
- 从 macro_series（FRED 已采：DXY DTWEXBGS / 黄金 GOLDAMGBD228NLBM / 利率 DFF / 10Y DGS10 / VIX）
  与 BTC 1d 收盘做「滞后互相关」：corr(macro_ret[t-k], btc_ret[t]) k=0..10，
  取最大 |corr| 的 k 为领先天数、符号为方向。
- 合成 tailwind 三元态（up/flat/down）：多序列多数决。
月频更新（调用方节流），纯规则、无前视（只用滞后关系）、写报告观测。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# FRED series_id → 方向约定（该序列上涨时对 BTC 的顺/逆风）
_FRED_DIR = {
    "DTWEXBGS": -1,          # DXY 涨 → BTC 逆风
    "GOLDAMGBD228NLBM": +1,  # 黄金涨 → BTC 顺风（共同受流动性驱动）
    "DFF": -1,               # 利率涨 → 逆风
    "DGS10": -1,             # 10Y 涨 → 逆风
    "VIXCLS": -1,            # VIX 涨 → 逆风
}
_LAGS = 10


def _load_macro(db, series_id: str, days: int = 500) -> Optional[pd.Series]:
    try:
        from backend.database.connection import MarketSessionLocal
        from backend.database.models import MacroSeries
        _mdb = MarketSessionLocal()
        try:
            rows = _mdb.query(MacroSeries).filter(MacroSeries.series_id == series_id) \
            .order_by(MacroSeries.ts.desc()).limit(days).all()
            if len(rows) < 60:
                return None
            df = pd.DataFrame([{"ts": r.ts, "v": float(r.value)} for r in rows]).sort_values("ts")
            return pd.Series(df["v"].values, index=df["ts"])
        finally:
            _mdb.close()
    except Exception as e:
        logger.debug("[MacroTailwind] %s 加载失败: %s", series_id, e)
        return None


def _btc_daily(db, days: int = 500) -> Optional[pd.Series]:
    try:
        from backend.services.kline_data_service import kline_service
        rows = kline_service.get_klines_from_db("BTC", "1d", days)
        if not rows or len(rows) < 60:
            return None
        df = pd.DataFrame(rows)
        return pd.Series(pd.to_numeric(df["close"], errors="coerce").values)
    except Exception as e:
        logger.debug("[MacroTailwind] BTC 加载失败: %s", e)
        return None


def _lag_corr(macro_ret: np.ndarray, btc_ret: np.ndarray, max_lag: int = _LAGS) -> Tuple[float, float]:
    """滞后互相关：max_k |corr(macro_ret[t-k], btc_ret[t])|，返回 (best_k, best_corr)。"""
    n = min(len(macro_ret), len(btc_ret))
    if n < 60:
        return 0, 0.0
    m = macro_ret[-n:]
    b = btc_ret[-n:]
    best_k, best_c = 0, 0.0
    for k in range(0, max_lag + 1):
        if k == 0:
            c = float(np.corrcoef(m, b)[0, 1])
        else:
            c = float(np.corrcoef(m[:-k], b[k:])[0, 1])
        if np.isfinite(c) and abs(c) > abs(best_c):
            best_k, best_c = k, c
    return best_k, best_c


def compute_macro_tailwind(db, symbol: str = "BTC") -> Dict[str, Any]:
    """顺风/逆风三元态（多序列多数决）。数据不足时返回 unknown。"""
    btc = _btc_daily(db)
    out: Dict[str, Any] = {"tailwind": "unknown", "details": [], "updated": False}
    if btc is None or len(btc) < 60:
        return out
    btc_ret = btc.pct_change().dropna().values
    votes = []
    for sid, direction in _FRED_DIR.items():
        s = _load_macro(db, sid)
        if s is None or len(s) < 60:
            continue
        r = s.pct_change().dropna().values
        k, c = _lag_corr(r, btc_ret)
        # 方向：macro 涨 → btc 涨 为顺风（c>0 且 direction=+1，或 c<0 且 direction=-1）
        tail = "up" if c * direction > 0.02 else ("down" if c * direction < -0.02 else "flat")
        votes.append(tail)
        out["details"].append({
            "series": sid, "best_lag_days": int(k),
            "corr": round(float(c), 4), "direction": tail,
        })
    if not votes:
        return out
    up = votes.count("up")
    down = votes.count("down")
    if up > down and up >= 2:
        out["tailwind"] = "up"
    elif down > up and down >= 2:
        out["tailwind"] = "down"
    else:
        out["tailwind"] = "flat"
    out["updated"] = True
    return out
