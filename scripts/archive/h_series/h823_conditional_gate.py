# -*- coding: utf-8 -*-
"""用成交条件期望写进场门。

做多只在「买一被打到之后」的盯市期望为正时允许。
做空只在「卖一被打到之后」的盯市期望为正时允许。
数字取时间后三分之一，前面的行情不参与开门。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
from backend.services.market_maker.flow_rules import (  # noqa: E402
    STEP_SEC, n_eff, oos_conditional_mean,
)
import psycopg  # noqa: E402

HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
HORIZON_S = 30
MARGIN_BP = 1.0


def load(conn, sym: str, lo_ms: int):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker"
            " WHERE symbol=%s AND event_ts_ms>%s AND bid_px>0 AND ask_px>bid_px"
            " ORDER BY event_ts_ms",
            (sym + "USDT", lo_ms),
        )
        bk = cur.fetchall()
        cur.execute(
            "SELECT event_ts_ms, price FROM asterdex_trades"
            " WHERE symbol=%s AND event_ts_ms>%s ORDER BY event_ts_ms",
            (sym + "USDT", lo_ms),
        )
        tr = cur.fetchall()
    if len(bk) < 500 or len(tr) < 200:
        return None
    bts = np.array([r[0] for r in bk], dtype=np.int64)
    bid = np.array([float(r[1]) for r in bk])
    ask = np.array([float(r[2]) for r in bk])
    tts = np.array([r[0] for r in tr], dtype=np.int64)
    px = np.array([float(r[1]) for r in tr])
    return bts, bid, ask, tts, px


def sides(bts, bid, ask, tts, px, horizon_s=HORIZON_S):
    mid = (bid + ask) / 2.0
    horizon = horizon_s * 1000
    step = int(STEP_SEC * 1000)
    grid = np.arange(int(bts[0]) + 60_000, int(bts[-1]) - horizon, step)
    idx = np.searchsorted(bts, grid, side="right") - 1
    ok = idx >= 0
    grid, idx = grid[ok], idx[ok]
    b0, a0, m0 = bid[idx], ask[idx], mid[idx]
    idx_h = np.searchsorted(bts, grid + horizon, side="right") - 1
    ok = (idx_h > idx) & (idx_h < len(mid))
    grid, b0, a0, m0, idx_h = grid[ok], b0[ok], a0[ok], m0[ok], idx_h[ok]
    m1 = mid[idx_h]
    lo = np.empty(len(grid))
    hi = np.empty(len(grid))
    for i, t0 in enumerate(grid):
        left = np.searchsorted(tts, t0, side="right")
        right = np.searchsorted(tts, t0 + horizon, side="right")
        if right <= left:
            lo[i] = np.nan
            hi[i] = np.nan
        else:
            sl = px[left:right]
            lo[i] = sl.min()
            hi[i] = sl.max()
    y_buy = (m1 - b0) / b0 * 1e4
    y_sell = (a0 - m1) / a0 * 1e4
    hit_bid = np.isfinite(lo) & (lo <= b0)
    hit_ask = np.isfinite(hi) & (hi >= a0)
    split = len(grid) * 2 // 3
    embargo = max(1, int(horizon_s / STEP_SEC))
    buy_mean, buy_n = oos_conditional_mean(y_buy, hit_bid, split, embargo)
    sell_mean, sell_n = oos_conditional_mean(y_sell, hit_ask, split, embargo)
    return {
        "buy": {"mean_y": buy_mean, "n": buy_n,
                "n_eff": n_eff(buy_n, STEP_SEC, horizon_s)},
        "sell": {"mean_y": sell_mean, "n": sell_n,
                 "n_eff": n_eff(sell_n, STEP_SEC, horizon_s)},
    }


def main() -> int:
    lo = int((time.time() - HOURS * 3600) * 1000)
    screen = json.loads((ROOT / "data" / "vol_top20.json").read_text(encoding="utf-8"))
    coins = []
    for row in sorted(screen.get("detail") or [], key=lambda r: -float(r.get("quote_volume_usd") or 0)):
        if float(row.get("spread_bp") or 99) >= 20:
            continue
        coins.append(str(row["symbol"]).upper())
        if len(coins) >= 12:
            break
    extra = [s.strip().upper() for s in (sys.argv[2].split(",") if len(sys.argv) > 2 else []) if s.strip()]
    for sym in extra:
        if sym not in coins:
            coins.append(sym)
    out = {"ts": time.time(), "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "hours": HOURS, "protocol": "conditional_fill_oos",
           "horizon_sec": HORIZON_S, "gates": {}}
    with psycopg.connect(_market_dsn(), autocommit=True) as conn:
        for sym in coins:
            try:
                loaded = load(conn, sym, lo)
            except Exception as exc:
                out["gates"][sym] = {"allow": False, "side": None, "mu": None,
                                     "reason": f"err:{str(exc)[:40]}"}
                print(f"  {sym:<8} 失败 {exc}")
                continue
            if loaded is None:
                out["gates"][sym] = {"allow": False, "side": None, "mu": None,
                                     "reason": "insufficient_data"}
                print(f"  {sym:<8} 数据不足")
                continue
            both = sides(*loaded)
            best_side = None
            best = None
            for side, stat in both.items():
                mean_y = stat["mean_y"]
                if mean_y is None:
                    continue
                if best is None or mean_y > best["mean_y"]:
                    best, best_side = stat, side
            allow = False
            if best is not None and best["mean_y"] > MARGIN_BP and best["n_eff"] >= 30:
                allow = True
            entry = {
                "allow": allow,
                "side": best_side if allow else None,
                "mu": None if best is None else round(best["mean_y"], 3),
                "max_hold_sec": float(HORIZON_S),
                "conditional": {
                    k: {"mean_y": None if v["mean_y"] is None else round(v["mean_y"], 3),
                        "n": v["n"], "n_eff": round(v["n_eff"], 2)}
                    for k, v in both.items()
                },
            }
            if allow:
                entry["oos"] = {
                    "mean_y": round(best["mean_y"], 3),
                    "n_eff": round(best["n_eff"], 2),
                    "win_rate": None,
                }
            else:
                entry["reason"] = "conditional_not_positive"
            out["gates"][sym] = entry
            b, s = both["buy"], both["sell"]
            def fmt(st):
                if st["mean_y"] is None:
                    return "n/a"
                return f"{st['mean_y']:+.2f}bp n_eff={st['n_eff']:.0f}"
            print(f"  {sym:<8} 做多 {fmt(b)}  做空 {fmt(s)}  "
                  f"{'开门('+best_side+')' if allow else '不开门'}")
    # 盯市到中间价会把还没买回的价差算成利润。排队之后这个数不是进场门。
    path = ROOT / "data" / "flow_gate_conditional.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    n_open = sum(1 for v in out["gates"].values() if v.get("allow"))
    print(f"已写研究记录 data/flow_gate_conditional.json，不覆盖进场门。打开 {n_open}/{len(out['gates'])}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
