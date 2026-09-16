# -*- coding: utf-8 -*-
"""[调研轮22 2026-09-17] **入场方案的真实结果仿真**（含出场逻辑），给 A/B 决策点定量答案。

对近 N 天**中线(mid)** 每笔入场信号，用 15m K 线 + 统一出场模型回放三种入场方案：

  V0 立即市价（现状基准）
  V1 挂 entry∓x 限价，TTL 内未成交 ⇒ **放弃**（= 方案B：等不到更好的价格就不追）
  V2 挂 entry∓x 限价，TTL 内未成交 ⇒ **市价兜底**（= 方案A：笔数不减）

统一出场模型（三方案完全相同，只隔离"入场"这一个变量）：
  硬止损 = entry×(1∓2%)（轮15b 后 mid 上限口径）
  分段止盈 = +0.8% / +1.6% / +3.0%，各平 30%/30%/40%
  先判止损后判止盈（保守）；往返成本 8bp × 名义
  最长持有 = 该仓实际持有小时数（上限 48h），超出则按最后一根收盘平掉
金额口径：PnL = 名义 × 价格变动% − 成本；名义 = 实际 margin × leverage。
只读，不改任何状态。
"""
import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.market_data import get_kline_data  # noqa: E402
from sqlalchemy import text  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=float, default=10.0)
ap.add_argument("--tier", default="mid")
ap.add_argument("--sl-pct", type=float, default=0.02)
ap.add_argument("--fees", type=float, default=0.0008)
args = ap.parse_args()

TP = [(0.008, 0.3), (0.016, 0.3), (0.030, 0.4)]


def bars(sym):
    try:
        rs = get_kline_data(sym, period="15m", count=900) or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rs:
        try:
            out.append({"ts": float(r.get("timestamp") or 0), "h": float(r.get("high") or 0),
                        "l": float(r.get("low") or 0), "c": float(r.get("close") or 0)})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["ts"])
    return out


def run_exit(entry, is_long, bars_after, max_bars):
    """返回 (收益率(价格口径, 已扣成本), 出场原因)。"""
    stop = entry * (1 - args.sl_pct) if is_long else entry * (1 + args.sl_pct)
    left = 1.0
    realized = 0.0
    tp_i = 0
    for b in bars_after[:max_bars]:
        if is_long:
            if b["l"] <= stop:                      # 先判止损（保守）
                realized += left * (-args.sl_pct)
                left = 0.0
                return realized - args.fees, "sl"
            while tp_i < len(TP) and b["h"] >= entry * (1 + TP[tp_i][0]):
                w = min(TP[tp_i][1], left)
                realized += w * TP[tp_i][0]
                left -= w
                tp_i += 1
                if left <= 1e-9:
                    return realized - args.fees, "tp"
        else:
            if b["h"] >= stop:
                realized += left * (-args.sl_pct)
                left = 0.0
                return realized - args.fees, "sl"
            while tp_i < len(TP) and b["l"] <= entry * (1 - TP[tp_i][0]):
                w = min(TP[tp_i][1], left)
                realized += w * TP[tp_i][0]
                left -= w
                tp_i += 1
                if left <= 1e-9:
                    return realized - args.fees, "tp"
    if left > 0:
        last = bars_after[min(max_bars, len(bars_after)) - 1]["c"]
        mv = ((last - entry) / entry) if is_long else ((entry - last) / entry)
        realized += left * mv
    return realized - args.fees, "time"


db = SessionLocal()
try:
    rows = db.execute(text("""
        SELECT id, symbol, side, entry_price, margin, leverage,
               (extract(epoch from opened_at))::float AS oe,
               coalesce(expected_hold_hours, 24) AS hold_h,
               unrealized_pnl AS actual_pnl, to_char(opened_at,'MM-DD HH24:MI') AS op
        FROM paper_positions
        WHERE account_id=14 AND opened_at > now() - (:d * interval '1 day')
          AND lower(coalesce(timeframe_tier,'')) = :t
        ORDER BY opened_at
    """), {"d": args.days, "t": args.tier}).mappings().all()
finally:
    db.close()

cache = {}
samples = []
for r in rows:
    sym = str(r["symbol"]).upper()
    if sym not in cache:
        cache[sym] = bars(sym)
    bs = cache[sym]
    if not bs:
        continue
    t0 = float(r["oe"])
    is_long = str(r["side"]).lower() in ("long", "buy")
    sig = float(r["entry_price"] or 0)
    if sig <= 0:
        continue
    seg = [b for b in bs if b["ts"] > t0]
    if len(seg) < 5:
        continue
    max_bars = max(1, int(float(r["hold_h"]) * 3600 / 900))
    samples.append({"id": r["id"], "sym": sym, "long": is_long, "sig": sig, "seg": seg,
                    "max_bars": max_bars, "notional": float(r["margin"] or 0) * float(r["leverage"] or 0),
                    "actual": float(r["actual_pnl"] or 0), "op": r["op"]})

print(f"样本 {len(samples)} 笔（近 {args.days:g} 天 {args.tier}）")
print(f"统一出场：SL {args.sl_pct*100:.1f}% / TP 0.8-1.6-3.0%（30/30/40）/ 成本 {args.fees*1e4:.0f}bp\n")

# V0 基准
v0 = sum(s["notional"] * run_exit(s["sig"], s["long"], s["seg"], s["max_bars"])[0] for s in samples)
act = sum(s["actual"] for s in samples)
print(f"V0 立即市价（模型口径）= {v0:+.2f} | 实际（同批）= {act:+.2f}（对照模型是否可信）\n")

print(f"{'方案':<26}{'总PnL':>10}{'笔数':>6}{'均':>9}{'胜率':>7}")
for x in (0.003, 0.005, 0.008, 0.012):
    for ttl_min in (30, 60):
        for mode in ("skip", "market"):
            tot = 0.0
            n = 0
            wins = 0
            for s in samples:
                tmax = ttl_min * 60
                win = [b for b in s["seg"] if b["ts"] <= s["seg"][0]["ts"] + tmax]
                tgt = s["sig"] * (1 - x) if s["long"] else s["sig"] * (1 + x)
                hit = any((b["l"] <= tgt) if s["long"] else (b["h"] >= tgt) for b in win)
                if hit:
                    pnl = s["notional"] * run_exit(tgt, s["long"], s["seg"], s["max_bars"])[0]
                elif mode == "skip":
                    continue
                else:
                    fill = win[-1]["c"] if win else s["seg"][0]["c"]
                    pnl = s["notional"] * run_exit(fill, s["long"], s["seg"], s["max_bars"])[0]
                tot += pnl
                n += 1
                wins += 1 if pnl > 0 else 0
            tag = f"V{'1' if mode == 'skip' else '2'} {x*100:.1f}%/{ttl_min}min/{mode}"
            print(f"{tag:<26}{tot:>10.2f}{n:>6}{(tot/n if n else 0):>9.2f}"
                  f"{(wins/n*100 if n else 0):>6.0f}%")

print("\n注：V1 = 回踩不到就放弃（方案B）；V2 = 回踩不到转市价（方案A）。")
print("    三方案共用同一出场模型 ⇒ 差异只来自入场。")
