# -*- coding: utf-8 -*-
"""H321 实盘已实现盈亏归因（按移动库存法，不依赖腿配对）。

# 背景：lane_ledger 只有主动(taker)平仓带 exit_path（F340），被动(maker)平仓
   是普通报价成交、无通道标记（F335/F340 的历史包袱）。FIFO 腿配对不可行。
   本脚本按逐笔移动库存法算已实现盈亏：
     加仓 → 并入均价；减仓 → 按均价实现盈亏，按"减仓腿的 exit_path"归通道；
     被动减仓(exit_path 为空)统一归 'passive_exit'。
   费用：entry 腿费用并入其通道腿（被动腿配对不了 → 费用按逐笔计入所在通道的减仓腿）。

# 用法: python scripts/h321_live_leg_attribution.py [--hours 48]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h321_live_legs.json"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()

    import psycopg2

    conn = psycopg2.connect(dsn())
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry WHERE lane_id='mm_asterdex'")
    row = cur.fetchone()
    era = (row[0] if row else None) or f"now() - interval '{a.hours} hours'"
    print(f"时代起点(stats_since): {era}")

    cur.execute("""
        SELECT position_id, symbol,
               (meta_json->>'side') AS side,
               (meta_json->>'qty')::float8 AS qty,
               (meta_json->>'fill_px')::float8 AS fill_px,
               COALESCE(meta_json->>'exit_path', '') AS exit_path,
               fee_bp, notional, ts
        FROM lane_ledger
        WHERE lane_id='mm_asterdex' AND event='fill'
          AND ts >= (%s)::timestamptz
          AND position_id IS NOT NULL AND position_id <> ''
        ORDER BY symbol, position_id, ts
    """, (era,))
    rows = cur.fetchall()
    print(f"账本行: {len(rows)}")

    # 逐币逐仓位移动库存
    agg = defaultdict(lambda: {"n": 0, "usd": 0.0, "fee_usd": 0.0, "notional": 0.0,
                               "raw_bps": [], "hold_secs": []})
    sym_stat = defaultdict(lambda: {"realized": 0.0, "fee": 0.0})
    inventory = []   # 期末持仓 [(symbol, side, qty)]
    key_prev = None
    for r in rows:
        pos_id, sym, side, qty, px, ep, fee_bp, notional, ts = r
        key = (sym, pos_id)
        if key != key_prev:
            st = {"qty": 0.0, "cost": 0.0, "last_ts": ts}
            st_map = {}
        st = st_map.setdefault(key, st)
        sign = 1.0 if side == "buy" else -1.0
        dq = sign * qty
        fee_usd = (fee_bp or 0.0) / 1e4 * (notional or 0.0)
        ch = ep if ep else "passive_exit" if st["qty"] != 0 and dq * st["qty"] < 0 else "entry"
        if dq * st["qty"] < 0:
            # 减仓（或穿仓）
            red = min(abs(dq), abs(st["qty"]))
            if st["qty"] > 0:
                realized = (px - st["cost"]) * red
            else:
                realized = (st["cost"] - px) * red
            g = agg[ch]
            g["n"] += 1
            g["usd"] += realized
            g["fee_usd"] += fee_usd
            g["notional"] += px * red
            g["raw_bps"].append(realized / (px * red) * 1e4 if px * red > 0 else 0.0)
            g["hold_secs"].append(max(0.0, (ts - st["last_ts"]).total_seconds()))
            sym_stat[sym]["realized"] += realized
            sym_stat[sym]["fee"] += fee_usd
            # 更新库存
            st["qty"] -= red * (1.0 if st["qty"] > 0 else -1.0)
            st["last_ts"] = ts
            if abs(st["qty"]) < 1e-12:
                st["qty"] = 0.0
                st["cost"] = 0.0
        # 剩余部分：加仓（或穿仓后的反向开仓）
        rem = abs(dq) - (min(abs(dq), abs(st["qty"])) if dq * st["qty"] < 0 else 0.0)
        if rem > 1e-12 and st["qty"] * dq >= 0:
            new_qty = abs(st["qty"]) + rem
            st["cost"] = (st["cost"] * abs(st["qty"]) + px * rem) / new_qty
            st["qty"] = new_qty * sign
            st["last_ts"] = ts
        elif rem > 1e-12:   # 穿仓：先开反向
            st["cost"] = px
            st["qty"] = rem * sign
            st["last_ts"] = ts
        key_prev = key

    print(f"\n{'通道':<24} {'n':>5} {'已实现USD':>10} {'费USD':>8} {'加权bp':>9} {'中位bp':>9} {'中位持时s':>9}")
    tot_usd = sum(g["usd"] for g in agg.values())
    tot_fee = sum(g["fee_usd"] for g in agg.values())
    tot_n = sum(g["n"] for g in agg.values())
    for k in sorted(agg, key=lambda k: -agg[k]["usd"]):
        g = agg[k]
        bps = sorted(g["raw_bps"])
        holds = sorted(g["hold_secs"])
        med_bp = bps[len(bps) // 2] if bps else 0.0
        med_h = holds[len(holds) // 2] if holds else 0.0
        w_bp = (g["usd"] / g["notional"] * 1e4) if g["notional"] > 0 else 0.0
        print(f"{k:<24} {g['n']:>5} {g['usd']:>+10.2f} {g['fee_usd']:>8.2f} {w_bp:>+9.2f}"
              f" {med_bp:>+9.2f} {med_h:>9.0f}")
    print(f"\n合计: 已实现 {tot_usd:+.2f} USD，费用 {tot_fee:.2f} USD，减仓腿 {tot_n}")

    print(f"\n{'币':<10} {'已实现USD':>10} {'费USD':>8}")
    for k in sorted(sym_stat, key=lambda k: -sym_stat[k]["realized"]):
        print(f"{k:<10} {sym_stat[k]['realized']:>+10.2f} {sym_stat[k]['fee']:>8.2f}")

    OUT.write_text(json.dumps(
        {"era": era,
         "channels": {k: {"n": v["n"], "usd": round(v["usd"], 3),
                          "fee_usd": round(v["fee_usd"], 3),
                          "w_bp": round(v["usd"] / v["notional"] * 1e4, 3) if v["notional"] else None}
                      for k, v in agg.items()},
         "by_symbol": {k: {"realized": round(v["realized"], 3), "fee": round(v["fee"], 3)}
                       for k, v in sym_stat.items()}},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
