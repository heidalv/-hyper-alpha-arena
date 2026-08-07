"""按币种 / 周期统计 K 线的描述性分布 — Stage C2 交付物。

用途:
    读取 collect_symbol_klines.py 产出的 CSV，计算：
      - 日内振幅（high-low）/close 分布（P25/P50/P75/P90/P99）
      - ATR(14) 分布
      - 30 日滚动 realized vol
      - 相邻 K 线 log-return 的 σ, 偏度, 峰度
      - 最大连续涨 / 连续跌根数
    所有结果写 CSV，便于 Stage D 决策会议引用。不做任何参数推论。

用法:
    python scripts/analyze_symbol_statistics.py --input-dir data/market_klines \
        --output docs/research/market_statistics.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import sys
from pathlib import Path
from typing import Iterator

try:
    import numpy as np
    import pandas as pd
except ImportError:
    print("需要 numpy + pandas。请先 pip install -r requirements.txt", file=sys.stderr)
    sys.exit(2)


logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("analyze_stats")


def iter_csv_files(root: Path) -> Iterator[tuple[str, str, Path]]:
    for symbol_dir in sorted(root.iterdir()):
        if not symbol_dir.is_dir():
            continue
        for csv_file in sorted(symbol_dir.glob("*.csv")):
            interval = csv_file.stem
            yield symbol_dir.name, interval, csv_file


def compute_atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]).abs(),
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window, min_periods=window).mean()


def consecutive_extremes(log_returns: pd.Series) -> tuple[int, int]:
    """返回 (最长连续上涨根数, 最长连续下跌根数)."""
    up_max = dn_max = up = dn = 0
    for r in log_returns.fillna(0).values:
        if r > 0:
            up += 1; dn = 0
            up_max = max(up_max, up)
        elif r < 0:
            dn += 1; up = 0
            dn_max = max(dn_max, dn)
        else:
            up = dn = 0
    return up_max, dn_max


def stats_for_one(symbol: str, interval: str, path: Path) -> dict | None:
    try:
        df = pd.read_csv(path)
    except Exception as e:
        logger.warning(f"读取失败 {path}: {e}")
        return None
    if df.empty:
        return None

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    if df.empty:
        return None

    amplitude = (df["high"] - df["low"]) / df["close"]
    log_ret = np.log(df["close"] / df["close"].shift(1))
    atr = compute_atr(df, window=14)
    atr_pct = atr / df["close"]

    bars_per_day = {
        "1m": 1440, "3m": 480, "5m": 288, "15m": 96, "30m": 48,
        "1h": 24, "2h": 12, "4h": 6, "6h": 4, "8h": 3, "12h": 2, "1d": 1,
    }.get(interval, 24)
    realized_vol_window = 30 * bars_per_day
    rolling_vol = log_ret.rolling(realized_vol_window, min_periods=max(10, realized_vol_window // 3)).std() * math.sqrt(bars_per_day)

    up_streak, dn_streak = consecutive_extremes(log_ret)

    def q(s: pd.Series, p: float) -> float:
        v = s.dropna().quantile(p) if len(s.dropna()) else float("nan")
        return float(v)

    return {
        "symbol": symbol,
        "interval": interval,
        "bars": int(len(df)),
        "amp_p25": q(amplitude, 0.25),
        "amp_p50": q(amplitude, 0.50),
        "amp_p75": q(amplitude, 0.75),
        "amp_p90": q(amplitude, 0.90),
        "amp_p99": q(amplitude, 0.99),
        "atr_p25": q(atr_pct, 0.25),
        "atr_p50": q(atr_pct, 0.50),
        "atr_p75": q(atr_pct, 0.75),
        "atr_p90": q(atr_pct, 0.90),
        "atr_p99": q(atr_pct, 0.99),
        "realized_vol_30d_median": q(rolling_vol, 0.50),
        "realized_vol_30d_p90": q(rolling_vol, 0.90),
        "log_ret_sigma": float(log_ret.std(skipna=True)) if log_ret.notna().any() else float("nan"),
        "log_ret_skew": float(log_ret.skew()) if log_ret.notna().any() else float("nan"),
        "log_ret_kurt": float(log_ret.kurt()) if log_ret.notna().any() else float("nan"),
        "max_up_streak": int(up_streak),
        "max_down_streak": int(dn_streak),
    }


def correlation_matrix(root: Path, interval: str = "1h") -> pd.DataFrame | None:
    """算所有币 interval close 收益的相关矩阵."""
    dfs = {}
    for sym_dir in sorted(root.iterdir()):
        if not sym_dir.is_dir():
            continue
        path = sym_dir / f"{interval}.csv"
        if not path.exists():
            continue
        try:
            d = pd.read_csv(path, usecols=["open_time_ms", "close"])
            d["close"] = pd.to_numeric(d["close"], errors="coerce")
            d = d.dropna().sort_values("open_time_ms")
            d = d.drop_duplicates(subset=["open_time_ms"], keep="last")
            d = d.set_index("open_time_ms")
            d = d["close"].pct_change().rename(sym_dir.name)
            dfs[sym_dir.name] = d
        except Exception as e:
            logger.warning(f"相关矩阵跳过 {path}: {e}")
    if len(dfs) < 2:
        return None
    joined = pd.concat(dfs.values(), axis=1, join="inner").dropna()
    if joined.empty:
        return None
    return joined.corr()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="统计 K 线分布，输出 CSV")
    p.add_argument("--input-dir", default="data/market_klines")
    p.add_argument("--output", default="docs/research/market_statistics.csv")
    p.add_argument("--corr-interval", default="1h",
                   help="相关矩阵使用的周期，默认 1h")
    p.add_argument("--corr-output", default="docs/research/symbol_correlation_matrix.csv")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.input_dir)
    if not root.exists():
        logger.error(f"输入目录不存在: {root}。请先跑 collect_symbol_klines.py")
        return 1

    rows: list[dict] = []
    for sym, iv, path in iter_csv_files(root):
        logger.info(f"统计 {sym} {iv}")
        r = stats_for_one(sym, iv, path)
        if r:
            rows.append(r)

    if not rows:
        logger.error("没有任何统计结果")
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    logger.info(f"✓ 分布统计 → {out_path} ({len(rows)} 行)")

    corr_df = correlation_matrix(root, interval=args.corr_interval)
    if corr_df is not None:
        corr_out = Path(args.corr_output)
        corr_out.parent.mkdir(parents=True, exist_ok=True)
        corr_df.to_csv(corr_out)
        logger.info(f"✓ 相关矩阵 → {corr_out}")
    else:
        logger.warning("相关矩阵样本不足，跳过")

    return 0


if __name__ == "__main__":
    sys.exit(main())
