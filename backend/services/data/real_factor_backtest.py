"""
真实数据因子回测脚本（P1 因子研究验证）。

连 alpha_market DB，取 BTC 真实 3 年日线，跑因子回测产出真实 IC/ICIR/半衰期。

用法（在 backend 目录）：
    python -m backend.services.data.real_factor_backtest
    python -m backend.services.data.real_factor_backtest --symbol ETH --period 1h
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

# DB 直连（不依赖 SQLAlchemy session，轻量）
import psycopg

# [2026-08-15] 移除硬编码占位密码：优先读 .env 的 MARKET_DATABASE_URL；
# 未配置时再从环境变量拼装；两者皆无则退出并提示，不再用占位连接串。
import os as _os


def _resolve_db_url() -> str:
    url = _os.environ.get("MARKET_DATABASE_URL", "")
    if url:
        return url
    user = _os.environ.get("PGUSER", "db_admin")
    pwd = _os.environ.get("PGPASSWORD", "")
    host = _os.environ.get("PGHOST", "localhost")
    port = _os.environ.get("PGPORT", "5432")
    if not pwd:
        raise RuntimeError(
            "缺少数据库连接配置：请设置 MARKET_DATABASE_URL 或 PGPASSWORD "
            "（参考 .env 中 MARKET_DATABASE_URL=postgresql+psycopg://laobao:...@localhost:5432/alpha_market，"
            "将 +psycopg 前缀去掉即可用于 psycopg 直连）"
        )
    return f"postgresql://{user}:{pwd}@{host}:{port}/alpha_market"


DB_URL = None  # 惰性解析：见 load_real_klines 入口


def load_real_klines(symbol: str = "BTC", period: str = "1d",
                     exchange: str = "hyperliquid", db_url: str = None) -> pd.DataFrame:
    """从 alpha_market.crypto_klines 取真实 K线。"""
    if db_url is None:
        db_url = _resolve_db_url()
    conn = psycopg.connect(db_url, connect_timeout=10)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT timestamp, open_price, high_price, low_price, close_price, volume "
            "FROM crypto_klines WHERE symbol=%s AND period=%s AND exchange=%s "
            "ORDER BY timestamp",
            (symbol, period, exchange),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
    df = df.set_index("datetime").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df[["open", "high", "low", "close", "volume"]]


def run_factor_backtest(df: pd.DataFrame, symbol: str, period: str):
    """在真实 DataFrame 上跑因子回测。"""
    from backend.services.alpha.factor_compute import FactorComputeAgent
    from backend.services.contracts.types import Instrument
    from backend.services.factor_engine.evaluation import evaluate_factor

    inst = Instrument(symbol=symbol, venue="hyperliquid", kind="perp")
    agent = FactorComputeAgent(instrument=inst)

    # 注册一组经典因子
    factors = {
        "mom5": {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]},
        "mom10": {"op": "mean", "args": [{"f": "returns"}, {"c": 10}]},
        "mom20": {"op": "mean", "args": [{"f": "returns"}, {"c": 20}]},
        "vol20": {"op": "std", "args": [{"f": "returns"}, {"c": 20}]},
        "rsi_div": {"op": "div", "args": [
            {"op": "sub", "args": [{"c": 0}]},  # 占位简化
            {"op": "std", "args": [{"f": "returns"}, {"c": 14}]},
        ]},
        "vol_price_corr": {"op": "rank", "args": [
            {"op": "corr", "args": [{"f": "close"}, {"f": "volume"}, {"c": 10}]}
        ]},
        "ts_rank_close": {"op": "ts_rank", "args": [{"f": "close"}, {"c": 20}]},
        "decay_ret": {"op": "decay_linear", "args": [{"f": "returns"}, {"c": 10}]},
    }
    for name, ast in factors.items():
        try:
            agent.register(name, ast)
        except Exception:
            pass

    factor_series = agent.compute_series(df)

    # 评估：forward return 作为 target
    close = df["close"]
    fwd_horizons = [1, 3, 5]  # 1/3/5 期 forward

    print(f"\n{'='*70}")
    print(f"真实因子回测: {symbol} {period}  ({len(df)} 根, "
          f"{df.index[0].date()} ~ {df.index[-1].date()})")
    print(f"{'='*70}")
    print(f"{'因子':<16s} {'horizon':>8s} {'IC':>8s} {'RankIC':>8s} {'ICIR':>8s} {'半衰期':>6s} {'N':>6s}")
    print("-" * 70)

    for name, arr in factor_series.items():
        fseries = pd.Series(arr, index=df.index)
        for h in fwd_horizons:
            fwd = close.shift(-h) / close - 1
            fwd = fwd.dropna()
            fs = fseries.reindex(fwd.index).replace([np.inf, -np.inf], np.nan).dropna()
            common = fs.index.intersection(fwd.index)
            if len(common) < 30:
                continue
            result = evaluate_factor(name, fs.loc[common], fwd.loc[common])
            print(f"{name:<16s} {h:>8d} {result.ic_mean:>8.4f} {result.rank_ic_mean:>8.4f} "
                  f"{result.icir:>8.4f} {result.halflife_bars:>6d} {result.n_samples:>6d}")

    return factor_series


def main() -> int:
    ap = argparse.ArgumentParser(description="真实数据因子回测")
    ap.add_argument("--symbol", default="BTC")
    ap.add_argument("--period", default="1d")
    ap.add_argument("--exchange", default="hyperliquid")
    args = ap.parse_args()

    print(f"加载真实数据: {args.symbol} {args.period} from {args.exchange}...")
    df = load_real_klines(args.symbol, args.period, args.exchange)
    if df.empty:
        print(f"无数据: {args.symbol} {args.period} {args.exchange}")
        return 1
    print(f"加载 {len(df)} 根: {df.index[0].date()} ~ {df.index[-1].date()}")

    run_factor_backtest(df, args.symbol, args.period)
    return 0


if __name__ == "__main__":
    sys.exit(main())
