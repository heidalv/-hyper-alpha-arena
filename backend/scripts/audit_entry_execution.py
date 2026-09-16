# -*- coding: utf-8 -*-
"""[调研轮19] **入场执行质量**：若把市价开仓改成"挂小额回撤回踩限价"，能改善多少？

问题（用户定调）：亏损单几乎都是 `peak≈0`（一开就逆行）⇒ 不靠加过滤，而是**改善入场执行**。
做法：对近 N 天每笔开仓，用 15m K 线看**入场后 1h 内**价格是否回踩到 entry×(1−x)（多头）：
  * 会回踩 ⇒ 限价单能以更优价格成交（同一批单、不加任何过滤）；
  * 不回踩 ⇒ 需要市价兜底（否则丢单）。据此算"回踩可得率"与"改善幅度"。

只读。用法：
  python backend/scripts/audit_entry_execution.py --days 7
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


def bars(sym, period, count):
    try:
        rows = get_kline_data(sym, period=period, count=count) or []
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] {sym} {period}: {exc}")
        return []
    out = []
    for r in rows:
        try:
            out.append({"ts": float(r.get("timestamp") or 0), "o": float(r.get("open") or 0),
                        "h": float(r.get("high") or 0), "l": float(r.get("low") or 0),
                        "c": float(r.get("close") or 0)})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["ts"])
    return [b for b in out if b["c"] > 0]


ap = argparse.ArgumentParser()
ap.add_argument("--days", type=float, default=7.0)
ap.add_argument("--account", type=int, default=14)
args = ap.parse_args()

db = SessionLocal()
try:
    rows = db.execute(text("""
        SELECT id, symbol, side, entry_price, timeframe_tier, trade_nature,
               (extract(epoch from opened_at))::float AS oe,
               to_char(opened_at,'MM-DD HH24:MI') AS op,
               peak_pnl_pct, trough_pnl_pct, status, unrealized_pnl
        FROM paper_positions
        WHERE account_id = :a AND opened_at > now() - (:d * interval '1 day')
        ORDER BY opened_at
    """), {"a": args.account, "d": args.days}).mappings().all()
finally:
    db.close()

print(f"近 {args.days:g} 天开仓 {len(rows)} 笔（account={args.account}）")
cache = {}
offsets = (0.001, 0.002, 0.003, 0.005, 0.008)
filled = {o: 0 for o in offsets}
gains = {o: [] for o in offsets}
paths = []
for r in rows:
    sym = str(r["symbol"]).upper()
    if sym not in cache:
        cache[sym] = bars(sym, "15m", 800)
    bs = cache[sym]
    if not bs:
        continue
    t0 = float(r["oe"])
    seg = [b for b in bs if t0 <= b["ts"] <= t0 + 3600]      # 入场后 1h
    if not seg:
        continue
    is_long = str(r["side"]).lower() in ("long", "buy")
    entry = float(r["entry_price"] or 0)
    if entry <= 0:
        continue
    # 1h 内的最优回踩（多头看最低价，空头看最高价）
    best = min(b["l"] for b in seg) if is_long else max(b["h"] for b in seg)
    dip = (entry - best) / entry if is_long else (best - entry) / entry
    # 前 4h 的 MFE/MAE（相对 entry，价格口径）
    seg4 = [b for b in bs if t0 <= b["ts"] <= t0 + 4 * 3600]
    hi = max(b["h"] for b in seg4) if seg4 else 0
    lo = min(b["l"] for b in seg4) if seg4 else 0
    mfe = (hi - entry) / entry if is_long else (entry - lo) / entry
    mae = (lo - entry) / entry if is_long else (entry - hi) / entry
    paths.append({"id": r["id"], "sym": sym, "long": is_long, "dip": dip,
                  "mfe": mfe, "mae": mae, "pnl": float(r["unrealized_pnl"] or 0),
                  "status": r["status"], "op": r["op"]})
    for o in offsets:
        if dip >= o:
            filled[o] += 1
            gains[o].append(o)   # 成交价改善 = o（相对市价）

n = len(paths)
print(f"可用样本 {n} 笔\n")
print("== 入场后 1h 内的回踩深度（多头=最低价低于 entry 的比例）==")
dips = [p["dip"] for p in paths]
if dips:
    print(f"   均值 {statistics.mean(dips)*100:+.2f}% | 中位 {statistics.median(dips)*100:+.2f}% | "
          f"最深 {max(dips)*100:+.2f}% | 回踩>0 的比例 {sum(1 for d in dips if d>0)/n*100:.0f}%")

print("\n== 若挂 entry×(1−x) 的限价（多头；空头对称），1h 内成交率与价格改善 ==")
print(f"   {'x':>6}{'成交率':>9}{'平均改善(成交者)':>18}")
for o in offsets:
    rate = filled[o] / n * 100 if n else 0
    print(f"   {o*100:>5.1f}%{rate:>8.0f}%{(o*100):>17.2f}%")

print("\n== 入场后前 4h 路径（价格口径）==")
if paths:
    print(f"   MFE 均值 {statistics.mean([p['mfe'] for p in paths])*100:+.2f}% | "
          f"MAE 均值 {statistics.mean([p['mae'] for p in paths])*100:+.2f}% | "
          f"MAE 深于 2% 比例 {sum(1 for p in paths if p['mae'] < -0.02)/n*100:.0f}%")

print("\n== 明细（最近 12 笔）==")
for p in paths[-12:]:
    print(f"   {p['op']} #{p['id']} {p['sym']:<7}{'long' if p['long'] else 'short':<6}"
          f"1h回踩={p['dip']*100:+5.2f}%  MFE={p['mfe']*100:+5.2f}% MAE={p['mae']*100:+6.2f}% "
          f"pnl={p['pnl']:+7.2f} {p['status']}")
