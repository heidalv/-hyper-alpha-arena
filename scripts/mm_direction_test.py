"""L1 方向性检验：持仓路径 × 后续行情 —— 判断亏损到底是不是"方向站反"。

[F197 2026-09-15] 为什么要单独做这个检验
--------------------------------------------------
前面的按小时归因已经排除了一个**朴素解释**：账本每个小时末的净敞口几乎都是 0
（+5$ / −27$ / +113$ 这种量级），而价格项却亏了 −442$ ⇒ **亏损不是"持有一整小时的长仓被趋势打"
** ✗，而是**开仓后很短时间内就被单边行情打掉、然后被平掉** ✓✓。
（最惨的一小时 09-15 13:00：名义 $90,808、净 −$63.81、价格项 −$97.09，
而**篮子只动了 −0.9bp** ⇒ 与"趋势"无关 ✗，是"每一笔成交之后都朝反方向走" ✓。）

要把"方向"这件事说清楚，必须用**持仓路径**（逐笔重建仓位、按 15s 中价盯市），回答两问：
  ① 我们的持仓在**下一段时间**的行情里，是**顺**还是**逆**？（若系统性逆 ⇒ 成交流是"知情"的，
     被动做市在被逆向选择 ⇒ 需要外部方向信号来"别站在错的一边" ✓）
  ② 在没有方向信号的情况下，"持仓期盯市盈亏"能解释多少账本价格项？（闭环校验 ✓）

口径：
  · 仓位 = 逐笔按 meta_json.side/qty 累加 ✓（买正卖负，单位=币）；
  · 盯市 = 15s 快照中价 ✓；每个"下一段"取 15 分钟（≈ 900s，做市持仓的数十倍 ✓）；
  · 顺逆判定 = sign(持仓) × 后续收益 ✓，>0 = 顺（赚），<0 = 逆（被打）✓。
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
EQUITY = 300.0
HORIZON_S = 900.0      # 15 分钟
SAMPLE_S = 60.0        # 每 60s 采一个持仓/价格点


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
    print(f"L1 方向性检验 · {LANE} · 起点 {since}  视野 {HORIZON_S/60:.0f} 分钟")
    fills = _rows("""SELECT ts, symbol, notional, price_bp, net_bp, meta_json FROM lane_ledger
                     WHERE lane_id=:l AND event='fill' AND ts >= :a ORDER BY ts""",
                  l=LANE, a=since_dt)
    print(f"成交 {len(fills)} 笔")
    if not fills:
        return 1

    # 逐币：仓位序列（按成交累加）
    pos_events: Dict[str, List[Tuple[float, float]]] = defaultdict(list)   # symbol -> [(ts_s, cum_qty)]
    for f in fills:
        ts = _dt(f["ts"]).timestamp()
        meta = f.get("meta_json") or {}
        q = float(meta.get("qty") or 0)
        side = str(meta.get("side") or "").lower()
        if not q:
            mid = float(meta.get("mid_px") or 0) or None
            q = (float(f["notional"] or 0) / mid) if mid else 0.0
        signed = q if side.startswith("b") else -q
        prev = pos_events[f["symbol"]][-1][1] if pos_events[f["symbol"]] else 0.0
        pos_events[f["symbol"]].append((ts, prev + signed))

    t0 = min(pe[0][0] for pe in pos_events.values() if pe)
    t1 = max(pe[-1][0] for pe in pos_events.values() if pe)
    lo, hi = int((t0 - 3600) * 1000), int((t1 + HORIZON_S + 3600) * 1000)

    print("\n【逐币：持仓路径 × 后续 15 分钟行情】")
    print(f"{'币':<6}{'采样点':>7}{'持仓时长占比':>13}{'平均|敞口|$':>12}{'最大|敞口|$':>12}"
          f"{'盯市盈亏$':>11}{'顺向占比':>10}{'逆>1bp占比':>12}{'方向期望bp':>12}")
    tot_mark = 0.0
    for s in SYMS:
        ser = []
        for r in _mrows("""SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots
                           WHERE symbol=:s AND timestamp BETWEEN :a AND :b ORDER BY timestamp""",
                        s=s, a=lo, b=hi):
            if r.get("best_bid") and r.get("best_ask"):
                ser.append((int(r["timestamp"]) / 1000.0, (float(r["best_bid"]) + float(r["best_ask"])) / 2))
        if len(ser) < 50:
            print(f"{s:<6} 行情不足")
            continue
        ts_arr = np.array([x[0] for x in ser])
        mid_arr = np.array([x[1] for x in ser])
        events = pos_events.get(s) or []
        if not events:
            continue
        ev_t = np.array([e[0] for e in events])
        ev_q = np.array([e[1] for e in events])

        samples = np.arange(t0, t1, SAMPLE_S)
        qty_s = np.zeros(len(samples))
        for i, tt in enumerate(samples):
            k = np.searchsorted(ev_t, tt, "right") - 1
            qty_s[i] = ev_q[k] if k >= 0 else 0.0
        mid_s = np.interp(samples, ts_arr, mid_arr)
        usd_s = qty_s * mid_s
        # 后续 HORIZON 的收益（bp）
        fwd = np.full(len(samples), np.nan)
        for i in range(len(samples)):
            j = np.searchsorted(ts_arr, samples[i] + HORIZON_S, "right") - 1
            if j > 0 and mid_s[i] > 0:
                fwd[i] = (mid_arr[j] - mid_s[i]) / mid_s[i] * 1e4
        ok = np.isfinite(fwd)
        hold = np.abs(usd_s) > 1e-9
        n_hold = int((hold & ok).sum())
        if n_hold < 10:
            print(f"{s:<6} 持仓采样不足（{n_hold}）")
            continue
        sign = np.sign(usd_s[hold & ok])
        rr = fwd[hold & ok]
        aligned = sign * rr                      # >0 顺，<0 逆
        # 盯市盈亏：持仓 × 价格变化（用相邻采样点，逐段累加）
        mark = float(np.nansum(usd_s[:-1] * (mid_s[1:] - mid_s[:-1]) / np.maximum(mid_s[:-1], 1e-9)))
        tot_mark += mark
        print(f"{s:<6}{n_hold:>7}{100*n_hold/len(samples):>12.1f}%{np.mean(np.abs(usd_s[hold])):>12.0f}"
              f"{np.max(np.abs(usd_s)):>12.0f}{mark:>+11.2f}{100*np.mean(aligned>0):>9.1f}%"
              f"{100*np.mean(aligned < -1):>11.1f}%{np.mean(aligned):>+12.3f}")

    # 闭环：盯市盈亏 vs 账本价格项
    led_price = sum(float(f["price_bp"] or 0) * float(f["notional"] or 0) / 1e4 for f in fills)
    led_net = sum(float(f["net_bp"] or 0) * float(f["notional"] or 0) / 1e4 for f in fills)
    print(f"\n【闭环校验】持仓期盯市盈亏 {tot_mark:+.2f}$  vs  账本价格项 {led_price:+.2f}$  "
          f"vs  账本净额 {led_net:+.2f}$")
    print("  解读：两者同量级 ⇒ 账本'价格项'确实=持有库存被行情打掉的盯市亏损 ✓（方向问题可量化）")

    # 汇总顺逆
    print("\n【结论口径】上面的'方向期望bp' = 平均( sign(持仓) × 后续15分钟收益 )：")
    print("  · 显著为负 ⇒ 我们倾向在**错误的一边**持仓（被知情流逆向选择）⇒ 外部方向信号有直接价值 ✓")
    print("  · 接近 0 ⇒ 持仓方向本身无偏 ⇒ 亏损来自'持有的量 × 波动'与费用，而非方向 ✗")
    print(f"  · 参考：账本每笔净 {led_net/sum(float(f['notional'] or 0) for f in fills)*1e4:+.3f}bp，"
          f"名义合计 ${sum(float(f['notional'] or 0) for f in fills):,.0f}（= 权益 {EQUITY:.0f}$ 的 "
          f"{sum(float(f['notional'] or 0) for f in fills)/EQUITY:.0f} 倍）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
