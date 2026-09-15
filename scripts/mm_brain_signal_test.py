"""LLM 主脑方向信号 → 做市车道的**实测**可用性检验（而不是照抄它的自我评价）。

[F202 2026-09-15] 为什么必须自己测
--------------------------------------------------
三路调研给出的事实互相张力很大：
  · 主脑输出契约很漂亮：`direction(bullish|bearish|neutral)` + `llm_conviction(0-100)`
    + `invalidation.price`（反向失效线）+ `expires_at`，落在 **alpha_analytics.mlto_thesis**
    （注意：不是 alpha_arena ✗），**只有 mid 档还活着**（long 停 09-14、short 停 09-08），
    五币齐全 ✓，直读 SQL 毫秒级、不调 LLM ✓。
  · 但它的**自我评价是差的**：`metrics.thesis_hit_rate = 0.312`（n=16）；
    `mlto_episodes` 自算 mid-long avg −1.39 / mid-short −4.58 / long-long −6.15；
    `brain.py:1409-1416` 引用审计（n=192）称 24h 胜率 0.434/0.343，**比抛硬币差** ✗。
  · L1 车道对主脑 **100% 零消费**（grep 0 命中）✗。

"别人说它不准"不等于"对做市没用" ✓ —— 判断标准只有两个，本脚本都测：
  ① **预测性**：主脑方向与 mm 五币**之后** 15/60/240 分钟的中价收益是否同向？
     （若显著为负 ⇒ **反着用**反而是 alpha ✓；若≈0 ⇒ 无信息 ✗）
  ② **对本车道成交质量的分层力**：按"成交方向 vs 主脑方向"分组，
     看**实际净额**（按名义加权 bp）是否有明显差异 ✓
     —— 只要分层力够强，就能当**准入/敞口闸门**用（哪怕它预测力弱 ✓）。

严格无未来函数：每笔成交只取 `updated_at <= 成交时刻` 的**最近一条**同币 mid 论题 ✓，
并记录它当时是否已过期（`expires_at`）✓ —— 过期率直接决定这条资源能不能实时用 ✓。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from backend.database.connection import MarketSessionLocal, SessionLocal  # noqa: E402

LANE = os.getenv("MM_LANE", "mm_asterdex")
SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL"]
TZ = timezone(timedelta(hours=8))
HORIZONS = [15, 60, 240]      # 分钟
ANALYTICS_URL = os.getenv(
    "ANALYTICS_DATABASE_URL",
    "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")


def _arena(sql: str, **p) -> List[dict]:
    with SessionLocal() as s:
        s.execute(text("SET statement_timeout = 60000"))
        return [dict(r) for r in s.execute(text(sql), p).mappings().all()]


def _market(sql: str, **p) -> List[dict]:
    with MarketSessionLocal() as s:
        s.execute(text("SET statement_timeout = 60000"))
        return [dict(r) for r in s.execute(text(sql), p).mappings().all()]


def _dt(v) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-09-14T11:50:00+08:00"
    since_dt = datetime.fromisoformat(since)
    eng = create_engine(ANALYTICS_URL, pool_pre_ping=True)

    # ── 1) 取主脑论题 ──
    with eng.connect() as c:
        c.execute(text("SET statement_timeout = 60000"))
        th = [dict(r) for r in c.execute(text(
            """SELECT symbol, tier, direction, llm_conviction, accepted, recommend_open,
                      should_close, invalidation_json, updated_at, expires_at
               FROM mlto_thesis WHERE symbol = ANY(:syms) ORDER BY updated_at"""),
            {"syms": SYMS}).mappings().all()]
    print(f"主脑论题 {len(th)} 条（symbols={SYMS}）")
    if th:
        tiers: Dict[str, int] = {}
        for r in th:
            tiers[str(r["tier"])] = tiers.get(str(r["tier"]), 0) + 1
        print(f"  按 tier: {tiers}；最近一条 updated_at={max(_dt(r['updated_at']) for r in th)}")
        print(f"  direction 分布: " + str({d: sum(1 for r in th if str(r['direction']) == d)
                                          for d in {str(r['direction']) for r in th}}))
    mid = [r for r in th if str(r["tier"]) == "mid"]
    mid_t: Dict[str, List[dict]] = {s: [] for s in SYMS}
    for r in mid:
        mid_t.setdefault(str(r["symbol"]), []).append(r)

    # ── 2) 成交 ──
    fills = _arena("""SELECT ts, symbol, notional, net_bp, meta_json FROM lane_ledger
                      WHERE lane_id=:l AND event='fill' AND ts >= :a ORDER BY ts""",
                   l=LANE, a=since_dt)
    print(f"车道成交 {len(fills)} 笔")
    if not fills or not mid:
        print("（缺少主脑 mid 论题或成交，无法检验）")
        return 1

    t0 = min(_dt(f["ts"]) for f in fills).timestamp()
    t1 = max(_dt(f["ts"]) for f in fills).timestamp()
    lo, hi = int((t0 - 600) * 1000), int((t1 + max(HORIZONS) * 60 + 600) * 1000)
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

    def thesis_asof(sym: str, when: datetime) -> Optional[dict]:
        best = None
        for r in mid_t.get(sym) or []:
            if _dt(r["updated_at"]) <= when:
                best = r
        return best

    DIR = {"bullish": 1.0, "long": 1.0, "bearish": -1.0, "short": -1.0, "neutral": 0.0}

    # ── 3) 预测性：主脑方向 vs 之后的中价收益 ──
    print("\n【① 预测性：主脑 mid 方向 → 之后中价收益（严格无未来函数）】")
    print(f"  {'视野':<8}{'样本':>7}{'同向率':>9}{'平均同向bp':>13}{'平均反向bp':>13}")
    pred_rows = []
    for sym in SYMS:
        ta, ma = mids.get(sym) or (np.array([]), np.array([]))
        for r in mid_t.get(sym) or []:
            d = DIR.get(str(r["direction"]).lower(), 0.0)
            if d == 0:
                continue
            t = _dt(r["updated_at"]).timestamp()
            j = int(np.searchsorted(ta, t, "right")) - 1
            if j < 0:
                continue
            m0 = float(ma[j])
            if m0 <= 0:
                continue
            row = {"sym": sym, "dir": d, "t": t, "conv": float(r["llm_conviction"] or 0)}
            for h in HORIZONS:
                k = int(np.searchsorted(ta, t + h * 60, "right")) - 1
                row[f"f{h}"] = d * (float(ma[k]) - m0) / m0 * 1e4 if k > j else np.nan
            pred_rows.append(row)
    for h in HORIZONS:
        v = [r[f"f{h}"] for r in pred_rows if np.isfinite(r[f"f{h}"])]
        if not v:
            continue
        v = np.array(v)
        print(f"  {str(h)+'min':<8}{len(v):>7}{100*np.mean(v > 0):>8.1f}%"
              f"{np.mean(v[v > 0]) if (v > 0).any() else float('nan'):>13.2f}"
              f"{np.mean(v[v < 0]) if (v < 0).any() else float('nan'):>13.2f}"
              f"    平均 {np.mean(v):+.2f}bp")

    # ── 4) 分层力：成交方向 vs 主脑方向 ──
    print("\n【② 分层力：按「成交方向 vs 主脑方向」分组的**实际净额**】")
    recs = []
    for f in fills:
        s = f["symbol"]
        side = str((f.get("meta_json") or {}).get("side") or "").lower()
        if not (side.startswith("b") or side.startswith("s")):
            continue
        t = _dt(f["ts"])
        th_r = thesis_asof(s, t)
        if not th_r:
            continue
        d = DIR.get(str(th_r["direction"]).lower(), 0.0)
        if d == 0:
            continue
        sign = 1.0 if side.startswith("b") else -1.0
        expired = _dt(th_r["expires_at"]) < t if th_r.get("expires_at") else True
        recs.append({"sym": s, "ntl": float(f["notional"] or 0), "net_bp": float(f["net_bp"] or 0),
                     "same": sign * d > 0, "expired": expired,
                     "conv": float(th_r["llm_conviction"] or 0)})
    if not recs:
        print("  无可配对样本")
        return 1

    def agg(rows: List[dict]) -> str:
        if not rows:
            return "n=0"
        ntl = sum(r["ntl"] for r in rows) or 1e-9
        return (f"n={len(rows):<5} 名义 ${ntl:>9,.0f} 净 "
                f"{sum(r['net_bp']*r['ntl'] for r in rows)/ntl:>+7.3f}bp")

    print(f"  可与主脑配对的成交: {len(recs)} 笔"
          f"（其中论题已过期 {sum(1 for r in recs if r['expired'])} 笔 = "
          f"{100*sum(1 for r in recs if r['expired'])/len(recs):.0f}%）")
    print(f"  同向（买在 bullish / 卖在 bearish）: {agg([r for r in recs if r['same']])}")
    print(f"  反向（买在 bearish / 卖在 bullish）: {agg([r for r in recs if not r['same']])}")
    for lo_c, hi_c, name in ((0, 45, "conviction<45"), (45, 55, "45~55"), (55, 101, ">55")):
        sub = [r for r in recs if lo_c <= r["conv"] < hi_c]
        if sub:
            print(f"  {name:<14}: {agg(sub)}")
    fresh = [r for r in recs if not r["expired"]]
    if fresh:
        print(f"  仅未过期论题: {agg(fresh)}（同向 {agg([r for r in fresh if r['same']])}）")

    print("\n读法：")
    print("  · ①里平均同向 bp 显著为负 ⇒ 主脑方向**反着用**才有 alpha ✓（它自评 0.43 胜率与此一致）")
    print("  · ②里同向/反向差异大 ⇒ 即便预测力弱，也能当**准入闸门**用 ✓")
    print("  · 过期占比高 ⇒ 实时性不足，只能当**慢变量**（限仓/上限），不能逐笔准入 ✗")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
