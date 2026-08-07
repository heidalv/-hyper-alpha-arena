"""中长线 Walk-Forward 薄钩子（P3，2026-07-31）。

用日线 EMA 趋势代理策略跑 WalkForwardAnalyzer，验证回测基建可挂中长线周期。
不替代实盘 MLTO；产出样本外 Sharpe/回撤/DSR，写入 midlong_reports/wfo_latest.json。

用法：
  python -m backend.scripts.midlong_walk_forward_hook
  或定时：run_and_save(symbols=['BTC','ETH','SOL'])
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class _EmaTrendStrategy:
    """日线 EMA 快慢线交叉：中长线趋势代理（非生产策略）。"""

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        p = params or {}
        self.fast = int(p.get("fast", 20))
        self.slow = int(p.get("slow", 50))

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data["close"].astype(float)
        ema_f = close.ewm(span=self.fast, adjust=False).mean()
        ema_s = close.ewm(span=self.slow, adjust=False).mean()
        sig = pd.Series(0, index=data.index, dtype=float)
        sig[ema_f > ema_s] = 1.0
        sig[ema_f < ema_s] = -1.0
        return sig

    def on_bar(self, bar, portfolio):
        return None


def _load_daily_ohlcv(symbol: str, lookback_days: int = 400) -> pd.DataFrame:
    from backend.services.data_center import data_center

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(lookback_days))
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")

    df = None
    try:
        df = data_center.get_klines_df(
            symbol.upper(), "1d", start=start_s, end=end_s, purpose="research",
        )
    except Exception:
        df = None

    if df is None or (hasattr(df, "empty") and df.empty):
        raw = data_center.get_klines(
            symbol.upper(), "1d", start=start_s, end=end_s, purpose="research",
        )
        rows = None
        if raw is None:
            return pd.DataFrame()
        if isinstance(raw, pd.DataFrame):
            df = raw.copy()
        elif hasattr(raw, "rows"):
            rows = getattr(raw, "rows", None) or []
            df = pd.DataFrame(rows)
        elif isinstance(raw, list):
            df = pd.DataFrame(raw)
        elif isinstance(raw, dict) and "rows" in raw:
            df = pd.DataFrame(raw.get("rows") or [])
        else:
            return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    # 统一列名
    colmap = {}
    for c in df.columns:
        cl = str(c).lower()
        if cl in ("open", "high", "low", "close", "volume"):
            colmap[c] = cl
        elif cl in ("timestamp", "ts", "open_time", "time", "datetime"):
            colmap[c] = "timestamp"
    df = df.rename(columns=colmap)
    if "timestamp" in df.columns:
        # unix 秒 or datetime
        ts = df["timestamp"]
        if pd.api.types.is_numeric_dtype(ts):
            df["timestamp"] = pd.to_datetime(ts, unit="s", utc=True, errors="coerce")
        else:
            df["timestamp"] = pd.to_datetime(ts, utc=True, errors="coerce")
        df = df.set_index("timestamp")
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True, errors="coerce")

    need = {"open", "high", "low", "close"}
    if not need.issubset(set(df.columns)):
        return pd.DataFrame()
    df = df.dropna(subset=["close"]).sort_index()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    # 索引名统一
    df.index.name = "datetime"
    return df


def _run_one_symbol(symbol: str, lookback_days: int = 400) -> Dict[str, Any]:
    from backend.services.backtest_engine import WalkForwardAnalyzer, WalkForwardConfig
    from backend.services.backtest_engine.backtest_engine import BacktestConfig

    df = _load_daily_ohlcv(symbol, lookback_days=lookback_days)
    if len(df) < 120:
        return {"symbol": symbol, "ok": False, "reason": f"insufficient bars={len(df)}"}

    def factory(params: Dict[str, Any]):
        return _EmaTrendStrategy(params)

    cfg = WalkForwardConfig(
        train_period_days=90,
        test_period_days=30,
        step_days=14,
        optimize_on_train=True,
        run_cscv=False,  # 周任务保持轻量
        run_dsr=True,
        backtest_config=BacktestConfig(initial_capital=10000.0),
    )
    analyzer = WalkForwardAnalyzer(cfg)
    param_grid = {"fast": [10, 20], "slow": [40, 50]}
    result = analyzer.analyze(factory, df, param_grid=param_grid)

    return {
        "symbol": symbol,
        "ok": True,
        "bars": len(df),
        "periods": len(result.periods),
        "oos_return": float(result.total_test_return or 0),
        "oos_sharpe": float(result.test_sharpe_ratio or 0),
        "oos_max_dd": float(result.test_max_drawdown or 0),
        "overfitting_score": float(result.overfitting_score or 0),
        "consistency_score": float(result.consistency_score or 0),
        "dsr": result.deflated_sharpe,
        "psr": result.probabilistic_sharpe,
        "pbo": result.pbo,
        "pbo_verdict": result.pbo_verdict or "",
    }


def run_and_save(
    symbols: Optional[List[str]] = None,
    lookback_days: int = 400,
    out_dir: str = "",
) -> str:
    """定时任务入口：跑 BTC/ETH/SOL WFO，写 wfo_latest.json。"""
    syms = symbols or ["BTC", "ETH", "SOL"]
    reports = []
    for sym in syms:
        try:
            reports.append(_run_one_symbol(sym, lookback_days=lookback_days))
            logger.info("[MidLongWFO] %s done ok=%s", sym, reports[-1].get("ok"))
        except Exception as e:
            logger.warning("[MidLongWFO] %s failed: %s", sym, e)
            reports.append({"symbol": sym, "ok": False, "reason": str(e)[:200]})

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "strategy": "ema_trend_proxy",
        "note": "代理趋势策略 WFO，用于证明闭环；非实盘 MLTO 参数。",
        "reports": reports,
    }
    _out_dir = out_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "midlong_reports",
    )
    os.makedirs(_out_dir, exist_ok=True)
    latest = os.path.join(_out_dir, "wfo_latest.json")
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    ts_path = os.path.join(
        _out_dir, f"wfo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )
    with open(ts_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    return latest


def main():
    logging.basicConfig(level=logging.INFO)
    path = run_and_save()
    print(f"[written] {path}")
    with open(path, encoding="utf-8") as f:
        print(f.read()[:2000])


if __name__ == "__main__":
    main()
