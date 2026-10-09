# -*- coding: utf-8 -*-
"""[h697 2026-10-01] 赚钱腿共性研究(用户:"怎么发现赚钱共性、阀口多少")。

方法:把今天每一条**开仓腿**(exit_path='')还原到**入场时刻**(quote_ts),
用市场数据重建入场条件(趋势 300s/900s、σ 相对基准、顺势/逆势、捕获),
按条件分桶看每腿净 bp —— 找出 n≥30 且净 bp 显著为正的条件组合,
输出可直接落地的"阀口"。

数据:lane_ledger(每腿结果)+ asterdex_book_ticker(5s 网格中价,重建入场环境)。
"""
from __future__ import annotations

import io
import math
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HOURS = 21.0
TICK_MS = 5000
TREND_WIN_MS = 300_000
TREND900_MS = 900_000


def _conn():
    from backend.services.market_maker.attribution import _market_dsn
    import psycopg
    return psycopg.connect(_market_dsn(), autocommit=True)


def _mids(cur, sym: str, t0_ms: int, t1_ms: int) -> Dict[int, float]:
    cur.execute(
        "SELECT (event_ts_ms/%s)*%s AS b,"
        " (array_agg((bid_px+ask_px)/2.0 ORDER BY event_ts_ms DESC))[1] AS mid"
        " FROM asterdex_book_ticker"
        " WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms < %s"
        "   AND bid_px > 0 AND ask_px > bid_px GROUP BY 1 ORDER BY 1",
        (TICK_MS, TICK_MS, f"{sym}USDT", t0_ms, t1_ms))
    return {int(r[0]): float(r[1]) for r in cur.fetchall() if r[1]}


def _nearest(ks: List[int], mids: Dict[int, float], t: int, tol: int) -> float:
    import bisect
    p = bisect.bisect_right(ks, t) - 1
    if p < 0 or t - ks[p] > tol:
        return float("nan")
    return mids[ks[p]]


def _vol20(mids: Dict[int, float], ks: List[int], t: int) -> float:
    """入场前 20 个 5s 桶的已实现波动(bp)。"""
    import bisect
    p = bisect.bisect_right(ks, t)
    seg = ks[max(0, p - 20):p]
    if len(seg) < 10:
        return float("nan")
    rets = [math.log(mids[seg[i + 1]] / mids[seg[i]]) for i in range(len(seg) - 1)]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / max(1, len(rets) - 1)
    return math.sqrt(var) * 1e4


def _bucket(vals: List[float]) -> dict:
    if len(vals) < 5:
        return {"n": len(vals), "mean": float("nan")}
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / max(1, len(vals) - 1)
    se = math.sqrt(var / len(vals)) if var > 0 else 0.0
    return {"n": len(vals), "mean": m, "t": m / se if se > 0 else 0.0}


def main() -> int:
    now_ms = int(time.time() * 1000)
    t0 = now_ms - int(HOURS * 3600 * 1000)
    # 1) 拉今天的开仓腿
    import importlib.util as _ilu
    import psycopg
    _spec = _ilu.spec_from_file_location("h425", ROOT / "scripts" / "h425_repair_trial.py")
    _h = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    with psycopg.connect(_h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
        from datetime import datetime, timedelta, timezone as _tz
        _cutoff = (datetime.now(_tz.utc) - timedelta(hours=HOURS)).isoformat()
        cur.execute(
            "SELECT ts, symbol, meta_json->>'side', spread_bp, price_bp, net_bp,"
            "       COALESCE((meta_json->>'quote_ts')::double precision,"
            "                extract(epoch from ts)::double precision)"
            " FROM lane_ledger WHERE lane_id='mm_asterdex' AND event='fill'"
            "   AND ts > %s"
            "   AND COALESCE(meta_json->>'exit_path','') = ''",
            (_cutoff,))
        legs = [(r[0], str(r[1]).upper(), str(r[2]).lower(), float(r[3]),
                 float(r[4]), float(r[5]), float(r[6])) for r in cur.fetchall()]
    print(f"开仓腿 {len(legs)} 条\n")
    # 2) 每币中价网格
    syms = sorted({s for _, s, *_ in legs})
    all_mids: Dict[str, Tuple[Dict[int, float], List[int]]] = {}
    with _conn() as c, c.cursor() as cur:
        for s in syms:
            d = _mids(cur, s, t0 - TREND900_MS, now_ms)
            all_mids[s] = (d, sorted(d))
    # 3) 重建入场条件
    rows: List[dict] = []
    for ts, s, side, cap, pr, net, qts in legs:
        mids, ks = all_mids.get(s, ({}, []))
        t = int(qts * 1000)
        mid0 = _nearest(ks, mids, t, 20_000)
        m300 = _nearest(ks, mids, t - TREND_WIN_MS, 30_000)
        m900 = _nearest(ks, mids, t - TREND900_MS, 30_000)
        vol = _vol20(mids, ks, t)
        if not (mid0 and mid0 > 0 and m300 and m300 > 0 and not math.isnan(vol)):
            continue
        trend = (mid0 - m300) / m300 * 1e4
        trend900 = (mid0 - m900) / m900 * 1e4 if (m900 and m900 > 0) else 0.0
        with_trend = (side == "buy" and trend > 0) or (side == "sell" and trend < 0)
        rows.append({"sym": s, "side": side, "ts": ts, "cap": cap, "pr": pr,
                     "net": net, "trend": trend, "trend900": trend900,
                     "vol": vol, "with_trend": with_trend})
    n = len(rows)
    print(f"可重建入场条件 {n} 条\n")

    def report(title: str, conds: List[Tuple[str, List[float]]]):
        print(f"===== {title} =====")
        for label, vals in conds:
            b = _bucket(vals)
            if b["n"] < 30 or math.isnan(b["mean"]):
                continue
            t = b.get("t", 0.0)
            flag = " ✅" if (b["mean"] > 0.5 and t > 1.5) else (" ❌" if (b["mean"] < -0.5 and t < -1.5) else "")
            print(f"  {label:<34} n={b['n']:>4}  净bp={b['mean']:+.3f} (t={t:+.1f}){flag}")
        print()

    # 4) 分桶
    # a) 捕获(挂宽)档
    caps = [(f"捕获{lo}-{hi}bp", [r["net"] for r in rows if lo <= r["cap"] < hi])
            for lo, hi in ((float("-inf"), 0.0), (0.0, 0.3), (0.3, 0.6),
                           (0.6, 1.0), (1.0, 2.0), (2.0, float("inf")))]
    report("A. 按捕获(挂宽)分档", caps)
    # b) 顺势 vs 逆势(按 |trend| 细分)
    wt = [r["net"] for r in rows if r["with_trend"]]
    at = [r["net"] for r in rows if not r["with_trend"]]
    report("B. 顺势 vs 逆势(入场时 5 分钟趋势方向)",
           [("顺势(买涨/卖跌)", wt), ("逆势(买跌/卖涨)", at)])
    # c) 趋势强度
    tre = [(f"趋势 {lo:+}-{hi:+}bp", [r["net"] for r in rows if lo <= r["trend"] < hi])
           for lo, hi in ((-50, -10), (-10, -3), (-3, 3), (3, 10), (10, 50))]
    report("C. 按入场趋势强度(5 分钟)", tre)
    # d) σ 档(vol bp 十分位)
    vols = sorted(r["vol"] for r in rows)
    qs = [vols[min(len(vols) - 1, len(vols) * i // 4)] for i in range(5)]
    vb = [(f"σ {qs[i]:.1f}-{qs[i+1]:.1f}bp", [r["net"] for r in rows if qs[i] <= r["vol"] < qs[i + 1]])
          for i in range(4)]
    report("D. 按入场 σ(20 桶已实现波动)", vb)
    # e) 组合:顺势 × 捕获
    report("E. 组合:顺势 × 捕获 ≥0.5bp × σ 下四分位",
           [("顺势+捕获≥0.5", [r["net"] for r in rows if r["with_trend"] and r["cap"] >= 0.5]),
            ("逆势+捕获<0.5", [r["net"] for r in rows if (not r["with_trend"]) and r["cap"] < 0.5]),
            ("顺势+捕获≥0.5+σ低", [r["net"] for r in rows
                                   if r["with_trend"] and r["cap"] >= 0.5 and r["vol"] < qs[1]])])
    # f) 15 分钟 fade 验证:trend900 极值后入场
    f900 = [(f"15分钟 {lo:+}-{hi:+}bp 后入场", [r["net"] for r in rows if lo <= r["trend900"] < hi])
            for lo, hi in ((-80, -30), (-30, 0), (0, 30), (30, 80))]
    report("F. 按 15 分钟趋势(验证 fade 方向)", f900)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
