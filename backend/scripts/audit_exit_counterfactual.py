# -*- coding: utf-8 -*-
"""[调研轮18 2026-09-16] **出场反事实**：平仓之后价格是继续沿原方向走（出场正确）
还是反转回来（砍太早）。

## 为什么需要

入场侧的闸门已被证明是对的（见 `audit_block_counterfactual.py`：拦掉的更差），
于是瓶颈落在"被放行的单 + 出场"上。7 天归因显示：

    breakeven_tp            n=14  +176.96        ← 唯一大额盈利通道
    sl                      n= 4  -113.18
    thesis_should_close     n= 9  - 97.38        ← thesis 类合计 -171.79，超过 sl
    thesis_invalidation     n= 4  - 74.41
    exit_policy:min_roi_decay n=3 - 37.45

`thesis_*` 类出场（论点失效/应平仓）已成为**最大合计亏损源**。本工具回答：
这些平仓之后，价格在 1h/4h/12h/24h 是**继续沿原方向**（⇒ 出场正确，避免了更大亏损）
还是**反转回来**（⇒ 砍太早，把本可回本的赢家送掉）。

口径：前向收益按**持仓原方向**计算（long: close(T+h)/close(T)-1），
所以 **负值 = 出场正确**，正值 = 出场偏早。含往返成本扣除后给出净额参考。

只读。用法：
  python backend/scripts/audit_exit_counterfactual.py --days 7
  python backend/scripts/audit_exit_counterfactual.py --days 7 --account 14 --detail 12
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _load_bars(symbol: str, period: str, count: int):
    from backend.services.market_data import get_kline_data

    try:
        rows = get_kline_data(symbol, period=period, count=count) or []
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] {symbol} K 线获取失败: {exc}")
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


def _idx_after(bars, t: float):
    for i, b in enumerate(bars):
        if b["ts"] >= t:
            return i
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=14)
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--horizons", default="1,4,12,24")
    ap.add_argument("--period", default="1h")
    ap.add_argument("--count", type=int, default=400)
    ap.add_argument("--fee-bps", type=float, default=8.0)
    ap.add_argument("--detail", type=int, default=0)
    args = ap.parse_args()

    horizons = [float(x) for x in str(args.horizons).split(",") if x.strip()]
    period_sec = {"1h": 3600.0, "15m": 900.0, "4h": 14400.0}.get(args.period, 3600.0)

    from backend.database.connection import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        rows = db.execute(text("""
            SELECT id, symbol, side, timeframe_tier, close_reason, unrealized_pnl,
                   entry_price, close_price, peak_pnl_pct, trough_pnl_pct,
                   (extract(epoch from closed_at))::float AS closed_epoch,
                   to_char(closed_at,'MM-DD HH24:MI') AS ct
            FROM paper_positions
            WHERE account_id = :a AND status = 'closed'
              AND closed_at > now() - (:d * interval '1 day')
            ORDER BY closed_at
        """), {"a": args.account, "d": args.days}).mappings().all()
    finally:
        db.close()

    print(f"近 {args.days:g} 天平仓 {len(rows)} 笔（account={args.account}）")
    if not rows:
        return 0

    syms = sorted({str(r["symbol"]).upper() for r in rows})
    bars_map = {}
    for s in syms:
        b = _load_bars(s, args.period, args.count)
        if b:
            bars_map[s] = b
    print(f"K 线可用 {len(bars_map)}/{len(syms)}")

    fee = args.fee_bps / 10000.0
    recs = []
    for r in rows:
        sym = str(r["symbol"]).upper()
        bars = bars_map.get(sym)
        if not bars or not r["closed_epoch"]:
            continue
        direction = 1 if str(r["side"]).lower() in ("long", "buy") else -1
        i0 = _idx_after(bars, float(r["closed_epoch"]))
        if i0 is None or i0 + 1 >= len(bars):
            continue
        entry = bars[i0]["o"]
        if entry <= 0:
            continue
        fwd = {}
        for h in horizons:
            j = i0 + int(round(h * 3600.0 / period_sec))
            fwd[h] = (direction * (bars[j]["c"] / entry - 1.0)) if j < len(bars) else None
        seg_end = min(i0 + int(round(max(horizons) * 3600.0 / period_sec)), len(bars) - 1)
        seg = bars[i0:seg_end + 1]
        mfe = (max(b["h"] for b in seg) / entry - 1.0) if direction > 0 else (entry / min(b["l"] for b in seg) - 1.0)
        mae = (min(b["l"] for b in seg) / entry - 1.0) if direction > 0 else (entry / max(b["h"] for b in seg) - 1.0)
        recs.append({
            "id": r["id"], "sym": sym, "dir": direction, "reason": str(r["close_reason"] or "-"),
            "pnl": float(r["unrealized_pnl"] or 0), "peak": r["peak_pnl_pct"], "trough": r["trough_pnl_pct"],
            "ct": r["ct"], "fwd": fwd, "mfe": mfe * direction, "mae": mae * direction,
        })

    print(f"可做反事实 {len(recs)} 笔\n")
    if not recs:
        return 0

    def _agg(vals):
        if not vals:
            return "n=0"
        return (f"n={len(vals):<4} mean={statistics.mean(vals)*100:+6.2f}% "
                f"med={statistics.median(vals)*100:+6.2f}% "
                f"反转(>0)={sum(1 for v in vals if v > 0)/len(vals)*100:5.1f}%")

    h_last = max(horizons)
    print("== 全样本：平仓后价格沿原方向的前向收益（负=出场正确）==")
    for h in horizons:
        print(f"   {h:>4.0f}h: {_agg([x['fwd'][h] for x in recs if x['fwd'][h] is not None])}")

    print(f"\n== 按平仓通道（{h_last:g}h 口径）==")
    g = defaultdict(list)
    for x in recs:
        g[x["reason"][:28]].append(x)
    for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
        vals = [x["fwd"][h_last] for x in v if x["fwd"][h_last] is not None]
        pnl = sum(x["pnl"] for x in v)
        print(f"   {k:<30}n={len(v):<3}当时净={pnl:+8.2f}  {_agg(vals)}")

    # 结论提示
    all24 = [x["fwd"][h_last] for x in recs if x["fwd"][h_last] is not None]
    if all24:
        rev = sum(1 for v in all24 if v > 0) / len(all24)
        print(f"\n提示：{h_last:g}h 后价格**反转回来**（出场偏早）的比例 = {rev*100:.0f}%；"
              f"均值 {statistics.mean(all24)*100:+.2f}%（负=出场避开了继续下跌/上涨）")

    if args.detail:
        print(f"\n== 明细（最近 {args.detail} 笔）==")
        for x in recs[-args.detail:]:
            f = x["fwd"][h_last]
            print(f"   {x['ct']} {x['sym']:<7}{'long' if x['dir'] > 0 else 'short':<6}"
                  f"{x['reason'][:24]:<26}当时={x['pnl']:+7.2f} "
                  f"peak={float(x['peak'] or 0)*100:+5.2f}% bust={float(x['trough'] or 0)*100:+6.2f}% "
                  f"{h_last:g}h后={('%+.2f%%' % (f*100)) if f is not None else 'n/a'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
