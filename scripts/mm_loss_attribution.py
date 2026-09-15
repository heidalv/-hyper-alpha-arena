"""L1 做市车道亏损归因：把"方向"这件事从账本里量出来。

[F196 2026-09-15] 要回答的问题
--------------------------------------------------
用户判断："高频交易是对的，但**有时候方向不对容易大亏**"。
体检分解显示：全时代 价差 **+4.40** / 价格 **−5.30** / 费用 −0.17 bp ⇒ 亏损几乎全部来自
**价格项（持仓期库存漂移）** ✓。本脚本把这个"价格项"拆开，回答：
  ① 亏损是**均匀**的（边际问题）还是**集中在少数时段**（方向问题）？
  ② 每小时价格项盈亏，能否被「该小时净敞口 × 篮子涨跌」解释？能 ⇒ **方向就是主因** ✓；
  ③ 最惨几小时里 5 个币是否**同向**（篮子相关性高 ⇒ 分散化救不了 ✗）；
  ④ 单笔名义 vs 权益（账本实测有 $184~$868 的单笔，权益才 $225 ⇒ 一次方向错判的杠杆有多大）。

账本结构（实测）：lane_ledger(lane_id, symbol, event, ts timestamptz, notional,
  spread_bp, funding_bp, price_bp, fee_bp, slippage_bp, points_usd, net_bp, meta_json)
  · **没有 side/qty/mid 列** ⇒ 它们在 `meta_json` 里（qty / side / mid_px）✓
  · **没有 net_usd 列** ⇒ 金额 = bp × notional / 1e4 ✓
  · price_bp 记在**减仓那一笔**上（整段持仓的漂移，见 core.InventoryBook.apply_fill）✓
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.database.connection import MarketSessionLocal, SessionLocal  # noqa: E402

LANE = os.getenv("MM_LANE", "mm_asterdex")
SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL"]
TZ = timezone(timedelta(hours=8))


def _rows(sql: str, **p) -> List[dict]:
    with SessionLocal() as s:
        s.execute(text("SET statement_timeout = 60000"))
        return [dict(r) for r in s.execute(text(sql), p).mappings().all()]


def _mrows(sql: str, **p) -> List[dict]:
    with MarketSessionLocal() as s:
        s.execute(text("SET statement_timeout = 60000"))
        return [dict(r) for r in s.execute(text(sql), p).mappings().all()]


def _dt(v) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-09-14T11:50:00+08:00"
    since_dt = datetime.fromisoformat(since)
    print(f"L1 亏损归因 · 车道 {LANE} · 起点 {since_dt.isoformat()}")

    fills = _rows("""SELECT ts, symbol, notional, spread_bp, price_bp, fee_bp, net_bp, meta_json
                     FROM lane_ledger WHERE lane_id=:l AND event='fill' AND ts >= :a ORDER BY ts""",
                  l=LANE, a=since_dt)
    print(f"成交 {len(fills)} 笔（名义合计 ${sum(float(f['notional'] or 0) for f in fills):,.0f}）")
    if not fills:
        return 1

    # ── 单笔名义分布（杠杆视角）──
    ntl = np.array([float(f["notional"] or 0) for f in fills])
    print(f"单笔名义: 中位 ${np.median(ntl):.0f} / p90 ${np.percentile(ntl,90):.0f} / 最大 ${ntl.max():.0f}"
          f"  ⇒ 相对 $300 权益 = {np.median(ntl)/300:.2f}× / {np.percentile(ntl,90)/300:.2f}× / {ntl.max()/300:.2f}×")

    hourly: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"n": 0, "ntl": 0.0, "net": 0.0, "spread": 0.0, "price": 0.0, "fee": 0.0})
    per_sym: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"n": 0, "ntl": 0.0, "net": 0.0, "spread": 0.0, "price": 0.0, "fee": 0.0})
    pos: Dict[str, float] = defaultdict(float)
    expo_end: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    last_ts: Dict[str, float] = {}

    for f in fills:
        ts = _dt(f["ts"])
        h = ts.astimezone(TZ).strftime("%m-%d %H:00")
        n = float(f["notional"] or 0)
        meta = f.get("meta_json") or {}
        side = str(meta.get("side") or "").lower()
        qty = float(meta.get("qty") or 0) or (n / float(meta.get("mid_px") or 1) if meta.get("mid_px") else 0.0)
        if side.startswith("b"):
            pos[f["symbol"]] += qty
        elif side.startswith("s"):
            pos[f["symbol"]] -= qty
        for bucket in (hourly[h], per_sym[f["symbol"]]):
            bucket["n"] += 1
            bucket["ntl"] += n
            bucket["net"] += float(f["net_bp"] or 0) * n / 1e4
            bucket["spread"] += float(f["spread_bp"] or 0) * n / 1e4
            bucket["price"] += float(f["price_bp"] or 0) * n / 1e4
            bucket["fee"] += float(f["fee_bp"] or 0) * n / 1e4
        last_ts[h] = max(last_ts.get(h, 0.0), ts.timestamp() * 1000)
        for s in SYMS:
            expo_end[h][s] = pos.get(s, 0.0)

    # ── 行情 ──
    t0 = min(_dt(f["ts"]) for f in fills)
    t1 = max(_dt(f["ts"]) for f in fills)
    lo, hi = int(t0.timestamp() * 1000) - 3.6e6, int(t1.timestamp() * 1000) + 3.6e6
    px: Dict[str, List[Tuple[int, float]]] = {}
    for s in SYMS:
        ser = []
        for r in _mrows("""SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots
                           WHERE symbol=:s AND timestamp BETWEEN :a AND :b ORDER BY timestamp""",
                        s=s, a=lo, b=hi):
            if r.get("best_bid") and r.get("best_ask"):
                ser.append((int(r["timestamp"]), (float(r["best_bid"]) + float(r["best_ask"])) / 2))
        px[s] = ser
    print("行情快照: " + ", ".join(f"{s}={len(px[s])}" for s in SYMS))

    def mid_at(s: str, ms: float) -> Optional[float]:
        best = None
        for t, m in px.get(s) or []:
            if t <= ms:
                best = m
            else:
                break
        return best

    # ── 逐小时表 ──
    print("\n" + "=" * 126)
    print(f"{'小时':<12}{'笔':>4}{'名义$':>10}{'净$':>9}{'价差$':>8}{'价格$':>9}{'费用$':>8}"
          f"{'期末净敞口$':>12}{'篮子bp':>8}{'敞口×涨跌$':>12}{'残差$':>9}")
    rows = []
    for h in sorted(hourly):
        b = hourly[h]
        end = last_ts.get(h, 0.0)
        start = end - 3.6e6
        basket, expo_usd, expl = [], 0.0, 0.0
        for s in SYMS:
            m0, m1 = mid_at(s, start), mid_at(s, end)
            ret = ((m1 - m0) / m0 * 1e4) if (m0 and m1) else 0.0
            if m0 and m1:
                basket.append(ret)
            usd = expo_end[h].get(s, 0.0) * (m1 or 0.0)
            expo_usd += usd
            expl += usd * ret / 1e4
        bb = float(np.mean(basket)) if basket else 0.0
        print(f"{h:<12}{b['n']:>4.0f}{b['ntl']:>10.0f}{b['net']:>+9.2f}{b['spread']:>+8.2f}"
              f"{b['price']:>+9.2f}{b['fee']:>+8.2f}{expo_usd:>+12.0f}{bb:>+8.1f}{expl:>+12.2f}"
              f"{b['price']-expl:>+9.2f}")
        rows.append((h, b["price"], expl, bb, expo_usd, b["net"]))

    # ── 方向性检验 ──
    y = np.array([r[1] for r in rows], dtype=float)
    x = np.array([r[2] for r in rows], dtype=float)
    print("\n【方向性检验】价格项(实际) 对 敞口×涨跌(方向解释)")
    if len(y) >= 4 and float(np.std(x)) > 0:
        print(f"  相关 ρ = {float(np.corrcoef(x,y)[0,1]):+.3f}   斜率 β = {float(np.polyfit(x,y,1)[0]):+.3f}"
              f"   （n={len(y)} 小时）")
    neg = [r for r in rows if r[1] < 0]
    tot = sum(r[1] for r in neg)
    worst = sorted(neg, key=lambda r: r[1])[:3]
    print(f"  价格项合计 {y.sum():+.2f}$；负向小时 {len(neg)} 个，合计 {tot:+.2f}$")
    print("  最惨 3 小时: " + " | ".join(f"{h} {p:+.2f}$ (篮子{bb:+.0f}bp, 敞口{eu:+.0f}$)"
                                    for h, p, _e, bb, eu, _n in worst))
    if tot < 0:
        print(f"  ⇒ 最惨 3 小时占总负向贡献 **{100*sum(r[1] for r in worst)/tot:.1f}%**")

    # ── 分币 ──
    print("\n【分币】")
    print(f"{'币':<6}{'笔':>6}{'名义$':>11}{'净$':>9}{'价差$':>9}{'价格$':>10}{'费用$':>9}{'净bp':>8}")
    for s in SYMS:
        b = per_sym.get(s)
        if not b:
            continue
        ntl_s = b["ntl"] or 1e-9
        print(f"{s:<6}{b['n']:>6.0f}{b['ntl']:>11.0f}{b['net']:>+9.2f}{b['spread']:>+9.2f}"
              f"{b['price']:>+10.2f}{b['fee']:>+9.2f}{b['net']/ntl_s*1e4:>+8.3f}")

    # ── 篮子同向性：最惨小时的 5 币涨跌 ──
    print("\n【最惨时段的方向一致性】（5 币是否一起走 ⇒ 分散化无效）")
    for h, p, _e, bb, _eu, _n in worst:
        end = last_ts.get(h, 0.0)
        rets = []
        for s in SYMS:
            m0, m1 = mid_at(s, end - 3.6e6), mid_at(s, end)
            if m0 and m1:
                rets.append((m1 - m0) / m0 * 1e4)
        if rets:
            same = sum(1 for r in rets if r * (1 if np.mean(rets) > 0 else -1) > 0)
            print(f"  {h}: 各币 {['%+.0f' % r for r in rets]} bp ⇒ {same}/5 同向，"
                  f"横截面σ={np.std(rets):.0f}bp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
