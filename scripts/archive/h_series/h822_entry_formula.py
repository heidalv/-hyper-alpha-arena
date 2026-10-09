# -*- coding: utf-8 -*-
"""进场期望的分解：无条件中间价漂移，不等于挂单成交之后的盈亏。

公式（价格用基点）:
  半价差 s = (卖一 - 买一) / (2 × 中间价) × 10000
  无条件漂移 μ = E[(中间价_{t+H} - 中间价_t) / 中间价_t] × 10000
  做多挂在买一，成交事件 F：之后 H 秒内有成交价 ≤ 当时买一
  成交后盯市 Y = s + 成交条件下的后续漂移
  进场能赚钱的条件是 E[Y | F] > 0，不是 μ 的符号
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _dsn():
    spec = importlib.util.spec_from_file_location(
        "h425", ROOT / "scripts" / "h425_repair_trial.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._market_dsn() if hasattr(mod, "_market_dsn") else None


def load(sym: str, hours: int):
    import psycopg
    from backend.services.market_maker.attribution import _market_dsn
    import time
    lo = int((time.time() - hours * 3600) * 1000)
    with psycopg.connect(_market_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker"
            " WHERE symbol=%s AND event_ts_ms>%s AND bid_px>0 AND ask_px>bid_px"
            " ORDER BY event_ts_ms",
            (sym + "USDT", lo),
        )
        bk = cur.fetchall()
        cur.execute(
            "SELECT event_ts_ms, price FROM asterdex_trades"
            " WHERE symbol=%s AND event_ts_ms>%s ORDER BY event_ts_ms",
            (sym + "USDT", lo),
        )
        tr = cur.fetchall()
    bts = np.array([r[0] for r in bk], dtype=np.int64)
    bid = np.array([r[1] for r in bk], dtype=np.float64)
    ask = np.array([r[2] for r in bk], dtype=np.float64)
    tts = np.array([r[0] for r in tr], dtype=np.int64)
    px = np.array([r[1] for r in tr], dtype=np.float64)
    return bts, bid, ask, tts, px


def summarize(sym: str, hours: int = 6, horizon_s: int = 30, step_s: int = 15):
    bts, bid, ask, tts, px = load(sym, hours)
    if len(bts) < 500 or len(tts) < 200:
        print(f"{sym}: 数据不足 book={len(bts)} trades={len(tts)}")
        return
    mid = (bid + ask) / 2.0
    horizon = horizon_s * 1000
    step = step_s * 1000
    grid = np.arange(bts[0] + 60_000, bts[-1] - horizon, step)
    # 每个网格点取当时盘口
    idx = np.searchsorted(bts, grid, side="right") - 1
    ok = idx >= 0
    grid, idx = grid[ok], idx[ok]
    b0, a0, m0 = bid[idx], ask[idx], mid[idx]
    half_bp = (a0 - b0) / m0 * 1e4 / 2.0
    # t+H 的中间价
    idx_h = np.searchsorted(bts, grid + horizon, side="right") - 1
    ok = (idx_h > idx) & (idx_h < len(mid))
    grid, b0, a0, m0, half_bp, idx_h = (
        grid[ok], b0[ok], a0[ok], m0[ok], half_bp[ok], idx_h[ok])
    m1 = mid[idx_h]
    mu = (m1 - m0) / m0 * 1e4
    # 窗口内成交的最低价、最高价
    lo = np.empty(len(grid))
    hi = np.empty(len(grid))
    for i, t0 in enumerate(grid):
        a = np.searchsorted(tts, t0, side="right")
        c = np.searchsorted(tts, t0 + horizon, side="right")
        if c <= a:
            lo[i] = np.nan
            hi[i] = np.nan
        else:
            sl = px[a:c]
            lo[i] = sl.min()
            hi[i] = sl.max()
    hit_bid = lo <= b0
    hit_ask = hi >= a0
    # 做多挂买一：成交后用 t+H 中间价盯市。Y = (m1 - b0) / b0
    y_buy = (m1 - b0) / b0 * 1e4
    y_sell = (a0 - m1) / a0 * 1e4
    def stat(mask, arr):
        x = arr[mask & np.isfinite(arr)]
        if len(x) == 0:
            return 0, float("nan")
        return int(len(x)), float(x.mean())
    n_all = len(mu)
    n_b, yb = stat(hit_bid, y_buy)
    n_a, ys = stat(hit_ask, y_sell)
    _, mu_b = stat(hit_bid, mu)
    _, mu_a = stat(hit_ask, mu)
    print(f"\n{sym}  {hours}h  H={horizon_s}s  网格 {n_all}")
    print(f"  半价差平均 {half_bp.mean():.2f} bp")
    print(f"  无条件漂移 μ = {mu.mean():+.3f} bp   （现在进场门用的就是这个数的符号）")
    print(f"  买一被打到 {n_b} 次 ({n_b/n_all*100:.0f}%)  成交后做多盯市 E[Y|打到买一] = {yb:+.3f} bp")
    print(f"    其中成交条件下的漂移 E[μ|打到买一] = {mu_b:+.3f} bp")
    print(f"  卖一被打到 {n_a} 次 ({n_a/n_all*100:.0f}%)  成交后做空盯市 E[Y|打到卖一] = {ys:+.3f} bp")
    print(f"    其中成交条件下的漂移 E[μ|打到卖一] = {mu_a:+.3f} bp")


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    for s in ("BTC", "ETH", "PLAY", "PENGU"):
        try:
            summarize(s, hours=6, horizon_s=30)
        except Exception as exc:
            print(f"{s} 失败: {exc}")
