# -*- coding: utf-8 -*-
"""[F38e 2026-09-11] 周进化因子方向序列预热脚本（多进程）。

背景：每周进化（NSGA-II）对 BTC/ETH × tier 周期做回测，单币全量预计算
~6.7h、8 模板 ≈54h，而适应度计算仅 ~10min —— 进化永远跑不完（"主脑进化
未知"根因）。live_pipeline_backtest_engine 已加磁盘缓存（data/factor_dir_cache，
按 (symbol,timeframe) 锚定最早 K 线、时间戳后缀复用）。本脚本在独立多进程中
把活跃 tier 的 (sym, tf) 序列提前算好，周一 01:00 的周进化命中磁盘缓存后
每模板预计算降到分钟级。

用法：
  backend\\.venv\\Scripts\\python.exe scripts/warm_factor_dir_cache.py [--workers 8]
  --symbols BTC,ETH --periods 5m,15m,1h,4h（默认覆盖 mid/short 全部 tier 周期）

注意：K 线修复任务（kline_repair）若改写历史 OHLCV，缓存可能与修复后的
数据不一致；缓存 TTL 7 天（PIPELINE_FACTOR_DIR_DISK_TTL_SEC），进化 fitness
是 genome 间相对比较，轻微漂移可容忍。
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

DAYS = 365
WARMUP = 30


def _worker(pair):
    sym, tf = pair
    try:
        from backend.services.live_pipeline_backtest_engine import (
            LivePipelineBacktestEngine, _factor_dir_disk_save,
        )
        from backend.services.strategy_evolver import StrategyEvolver
        bars = StrategyEvolver._load_bars(sym, tf, DAYS)
        if not bars or len(bars) <= WARMUP:
            return (sym, tf, f"no_bars({len(bars) if bars else 0})")
        engine = LivePipelineBacktestEngine(initial_capital=10000)
        series = [0] * WARMUP + [None] * (len(bars) - WARMUP)
        t0 = time.time()
        for i in range(WARMUP, len(bars)):
            series[i] = engine._compute_factor_direction_windowed(i, bars)
            if (i - WARMUP) % 5000 == 0:
                dt = time.time() - t0
                print(f"[warm {sym}/{tf}] {i - WARMUP}/{len(bars) - WARMUP} "
                      f"({100.0 * (i - WARMUP) / max(len(bars) - WARMUP, 1):.0f}%, "
                      f"elapsed {dt / 60:.1f}min)", flush=True)
        tss = [int(b.timestamp) for b in bars]
        _factor_dir_disk_save(sym, tf, tss, series)
        return (sym, tf, f"done bars={len(bars)} {((time.time() - t0) / 60):.1f}min")
    except Exception as e:
        return (sym, tf, f"ERROR {type(e).__name__}: {str(e)[:160]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=int(os.getenv("WARM_WORKERS", "8")))
    ap.add_argument("--symbols", type=str, default=os.getenv("WARM_SYMBOLS", "BTC,ETH"))
    ap.add_argument("--periods", type=str, default=os.getenv("WARM_PERIODS", "5m,15m,1h,4h"))
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    pairs = [(s, p) for s in syms for p in periods]
    print(f"[warm] pairs={pairs} workers={args.workers}", flush=True)

    # Windows 下 spawn 需要可导入的模块入口
    with mp.Pool(processes=min(args.workers, len(pairs))) as pool:
        for res in pool.imap_unordered(_worker, pairs):
            print(f"[warm] result: {res}", flush=True)
    print("[warm] all done", flush=True)


if __name__ == "__main__":
    mp.freeze_support()
    main()
