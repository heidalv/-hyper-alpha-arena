"""方向闸门有效性检验：把"逆势成交"和"顺势成交"分开，看 markout 差多少。

[F199 2026-09-15] 为什么先做这个实验，而不是先接因子/主脑
--------------------------------------------------
用户判断："高频是对的，但**方向不对容易大亏**"，并建议共享因子系统 / LLM 主脑 / 中长线的
方向资源。在动手接线之前必须先回答一个**可证伪**的问题：

    如果按"当时的大方向"过滤，成交质量会不会显著变好？

· 若**会**（逆势成交 markout 明显更差）⇒ 接方向闸门有价值 ✓，
  且可以先上**最便宜的代理量**（近期收益/趋势），再逐步换成因子/主脑的更精细判断 ✓；
· 若**不会**（逆势与顺势一样差）⇒ 说明毒流不是"趋势"这一维能解释的 ✗，
  接趋势类资源没用，必须找**别的维度**（波动、OFI、资金费率、持仓拥挤度…）✗✓。

做法：对每笔成交，用**成交之前**的信息（严格无未来函数）算一个方向代理：
    trend = 该币在过去 15 分钟的中价收益（bp）
    with_trend = sign(成交方向 × trend)      # 买在上涨/卖在下跌 = 顺势
然后比较两类成交的 markout（30s / 60s / 300s）与**账本实际净额**（按名义加权 bp）。
再按 |trend| 分档（弱/中/强），看"方向强度"是否单调地恶化 markout ✓。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.database.connection import MarketSessionLocal, SessionLocal  # noqa: E402

LANE = os.getenv("MM_LANE", "mm_asterdex")
SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL"]
TZ = timezone(timedelta(hours=8))
HORIZONS = [30, 60, 300]
TREND_WIN_S = 900.0     # 过去 15 分钟作为"方向"代理


def _rows(sql: str, **p) -> List[dict]:
    with SessionLocal() as s:
        s.execute(text("SET statement_timeout = 60000"))
        return [dict(r) for r in s.execute(text(sql), p).mappings().all()]


def _dt(v) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-09-14T11:50:00+08:00"
    since_dt = datetime.fromisoformat(since)
    fills = _rows("""SELECT ts, symbol, notional, net_bp, meta_json FROM lane_ledger
                     WHERE lane_id=:l AND event='fill' AND ts >= :a ORDER BY ts""",
                  l=LANE, a=since_dt)
    print(f"方向闸门有效性检验 · {LANE} · 起点 {since} · 成交 {len(fills)} 笔 · "
          f"方向代理 = 过去 {TREND_WIN_S/60:.0f} 分钟该币中价收益")
    if not fills:
        return 1

    t0 = min(_dt(f["ts"]) for f in fills).timestamp()
    t1 = max(_dt(f["ts"]) for f in fills).timestamp()
    lo, hi = int((t0 - TREND_WIN_S - 300) * 1000), int((t1 + max(HORIZONS) + 300) * 1000)
    mids: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for s in SYMS:
        ts_l, m_l = [], []
        with MarketSessionLocal() as ses:
            ses.execute(text("SET statement_timeout = 60000"))
            for r in ses.execute(text(
                    """SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots
                       WHERE symbol=:s AND timestamp BETWEEN :a AND :b ORDER BY timestamp"""),
                    {"s": s, "a": lo, "b": hi}).mappings().all():
                if r["best_bid"] and r["best_ask"]:
                    ts_l.append(int(r["timestamp"]) / 1000.0)
                    m_l.append((float(r["best_bid"]) + float(r["best_ask"])) / 2)
        mids[s] = (np.array(ts_l), np.array(m_l))

    recs = []
    for f in fills:
        s = f["symbol"]
        ta, ma = mids.get(s) or (np.array([]), np.array([]))
        if len(ta) < 10:
            continue
        side = str((f.get("meta_json") or {}).get("side") or "").lower()
        if not (side.startswith("b") or side.startswith("s")):
            continue
        t = _dt(f["ts"]).timestamp()
        j = int(np.searchsorted(ta, t, "right")) - 1
        jp = int(np.searchsorted(ta, t - TREND_WIN_S, "right")) - 1
        if j < 0 or jp < 0 or jp >= j:
            continue
        m0 = float(ma[j])
        if m0 <= 0:
            continue
        trend = (m0 - float(ma[jp])) / float(ma[jp]) * 1e4      # 过去 15 分钟收益 bp
        sign = 1.0 if side.startswith("b") else -1.0
        mos = {}
        for h in HORIZONS:
            k = int(np.searchsorted(ta, t + h, "right")) - 1
            mos[h] = sign * (float(ma[k]) - m0) / m0 * 1e4 if k > j else np.nan
        ntl = float(f["notional"] or 0)
        recs.append({"sym": s, "side": side, "sign": sign, "trend": trend,
                     "with": sign * trend > 0, "ntl": ntl,
                     "net_bp": float(f["net_bp"] or 0), **{f"mo{h}": mos[h] for h in HORIZONS}})
    print(f"可用于检验的成交 {len(recs)} 笔（需成交前 15 分钟有行情）")

    def agg(rows: List[dict]) -> str:
        if not rows:
            return "(无)"
        ntl = sum(r["ntl"] for r in rows) or 1e-9
        netbp = sum(r["net_bp"] * r["ntl"] for r in rows) / ntl
        s = f"n={len(rows):<5} 名义 ${ntl:>9,.0f}  实际净 {netbp:>+7.3f}bp"
        for h in HORIZONS:
            v = [r[f"mo{h}"] for r in rows if np.isfinite(r[f"mo{h}"])]
            s += f"  markout{h}s {np.mean(v):>+6.2f}bp" if v else f"  markout{h}s   n/a"
        return s

    with_t = [r for r in recs if r["with"]]
    against = [r for r in recs if not r["with"]]
    print("\n【顺势 vs 逆势】")
    print("  顺势（买在上涨/卖在下跌）: " + agg(with_t))
    print("  逆势（买在下跌/卖在上涨）: " + agg(against))
    d_net = (sum(r["net_bp"] * r["ntl"] for r in with_t) / (sum(r["ntl"] for r in with_t) or 1e-9)
             - sum(r["net_bp"] * r["ntl"] for r in against) / (sum(r["ntl"] for r in against) or 1e-9))
    print(f"  ⇒ 顺势 − 逆势 = {d_net:+.3f}bp（>0 表示顺势更好）")

    print("\n【按方向强度分档】（|过去15分钟收益|）")
    mags = np.array([abs(r["trend"]) for r in recs])
    if len(mags) > 20:
        qs = np.quantile(mags, [0.33, 0.66])
        bands = [("弱", mags <= qs[0]), ("中", (mags > qs[0]) & (mags <= qs[1])), ("强", mags > qs[1])]
        for name, mask in bands:
            sub = [r for r, m in zip(recs, mask) if m]
            print(f"  {name:<3}(n={len(sub):<5}) " + agg(sub))

    print("\n【只看逆势成交的强度】")
    if against:
        am = np.array([abs(r["trend"]) for r in against])
        if len(am) > 10:
            med = np.median(am)
            for name, mask in (("轻度逆势", am <= med), ("重度逆势", am > med)):
                sub = [r for r, m in zip(against, mask) if m]
                print(f"  {name}(n={len(sub):<5}) " + agg(sub))

    print("\n结论读法：")
    print("  · 若『逆势』的实际净额明显更差、且『重度逆势』最差 ⇒ 方向闸门（哪怕是趋势代理）有效 ✓")
    print("  · 若两者接近 ⇒ 毒流不是趋势维度 ⇒ 别接趋势类资源 ✗，改找波动/OFI/费率等维度 ✓")

    # ── 反事实：如果"跳过"某些成交，账面会变成什么样 ──
    #
    # ⚠️ 口径警告：这只是**一阶估计**——跳过一笔成交会改变后续仓位与对冲路径 ✗，
    # 所以它不是"闸门上线后的精确收益"，而是"这批成交本身贡献了多少盈亏"✓。
    # 之所以仍然值得算：它能立刻回答"这条路值不值得写代码" ✓。
    print("\n【反事实：按当前盈亏口径剔除某类成交后的账面净额】")
    tot_ntl = sum(r["ntl"] for r in recs) or 1e-9
    base = sum(r["net_bp"] * r["ntl"] for r in recs) / tot_ntl
    print(f"  基准（全部 {len(recs)} 笔）: 净 {base:+.3f}bp   名义 ${tot_ntl:,.0f}")

    def counter(name: str, drop) -> None:
        kept = [r for r in recs if not drop(r)]
        ntl = sum(r["ntl"] for r in kept) or 1e-9
        netbp = sum(r["net_bp"] * r["ntl"] for r in kept) / ntl
        print(f"  剔除{name}({len(recs)-len(kept)} 笔): 净 {netbp:+.3f}bp  "
              f"Δ={netbp-base:+.3f}bp  剩余名义 ${ntl:,.0f}")

    counter("全部顺势成交", lambda r: r["with"])
    for thr in (10.0, 20.0, 40.0):
        counter(f"|trend|>{thr:.0f}bp 的顺势成交",
                lambda r, t=thr: r["with"] and abs(r["trend"]) > t)
    counter("全部卖腿", lambda r: r["side"].startswith("s"))
    counter("顺势卖腿（卖在上涨）", lambda r: r["with"] and r["side"].startswith("s"))
    counter("逆势买腿（买在下跌）—— 保留最优质的一类",
            lambda r: not (r["with"] is False and r["side"].startswith("b")))
    print("  读法：Δ 明显为正 ⇒ 这条路值得写进策略 ✓；Δ≈0 ⇒ 剔除它只是少做交易、不改变边际 ✗")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
