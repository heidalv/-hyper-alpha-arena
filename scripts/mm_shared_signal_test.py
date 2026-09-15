"""共享方向资源统一实测：同一套框架比较「选币方向偏置」与「signal_ledger 方向」。

[F203 2026-09-15] 为什么统一框架
--------------------------------------------------
用户问"因子系统 / LLM 主脑 / 中长线有什么可以共享"。三路调研给了**清单**，
但清单里的每个资源都需要回答同一个可证伪的问题，否则接线就是凭感觉 ✗：
    Q1 **预测性**：它的方向与标的**之后**的走势同向吗？（严格无未来函数）
    Q2 **分层力**：按"我们的成交方向 vs 它的方向"分组，**实际净额**差异有多大？
        —— 只要分层力够强，哪怕预测力弱也能当**准入闸门** ✓。
    Q3 **实时性**：配对时有多少比例的信号在**当时已过期**？（决定能否逐笔用 ✗）

本脚本把剩下两条资源放进同一框架（主脑那条见 `mm_brain_signal_test.py` ✓）：
    A. `alpha_arena.coin_select_candidates`（horizon='midlong'）—— 选币平台的方向偏置
       `direction_bias(long/short/neutral)` + `confidence` + `score`；`created_at` 是
       **naive 本地墙钟**（实测坑，见 F193/调研），`valid_until` 同样 naive ✓ 覆盖 5 币 ✓。
    B. `alpha_arena.signal_ledger` —— 事件/图审信号的逐币方向 + `strength`(0–8 档 ✗ 量纲不同)
       + `payload.position_adjustments`（`no_new_long/no_new_short`，**已生产零消费者** ✓）；
       时间列是 `created_ms` **epoch 毫秒（无坑）** ✓。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.database.connection import MarketSessionLocal, SessionLocal  # noqa: E402

LANE = os.getenv("MM_LANE", "mm_asterdex")
SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL"]
TZ = timezone(timedelta(hours=8))
HORIZONS = [15, 60, 240]
DIRW = {"bullish": 1.0, "long": 1.0, "bearish": -1.0, "short": -1.0, "neutral": 0.0}


def _a(sql: str, **p) -> List[dict]:
    with SessionLocal() as s:
        s.execute(text("SET statement_timeout = 60000"))
        return [dict(r) for r in s.execute(text(sql), p).mappings().all()]


def _dt(v) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-09-14T11:50:00+08:00"
    since_dt = datetime.fromisoformat(since)
    fills = _a("""SELECT ts, symbol, notional, net_bp, meta_json FROM lane_ledger
                  WHERE lane_id=:l AND event='fill' AND ts >= :a ORDER BY ts""",
               l=LANE, a=since_dt)
    print(f"共享方向资源统一实测 · {LANE} · 成交 {len(fills)} 笔 · 起点 {since}")
    if not fills:
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

    def predictive(name: str, events: List[dict]) -> None:
        """events: [{sym, dir, t, meta}] —— 只统计有行情的。"""
        print(f"\n【{name} · ① 预测性】")
        print(f"  {'视野':<9}{'样本':>7}{'同向率':>9}{'平均同向bp':>13}")
        for h in HORIZONS:
            v = []
            for e in events:
                ta, ma = mids.get(e["sym"]) or (np.array([]), np.array([]))
                j = int(np.searchsorted(ta, e["t"], "right")) - 1
                if j < 0 or float(ma[j]) <= 0:
                    continue
                k = int(np.searchsorted(ta, e["t"] + h * 60, "right")) - 1
                if k > j:
                    v.append(e["dir"] * (float(ma[k]) - float(ma[j])) / float(ma[j]) * 1e4)
            if not v:
                continue
            v = np.array(v)
            print(f"  {str(h)+'min':<9}{len(v):>7}{100*np.mean(v > 0):>8.1f}%{np.mean(v):>13.2f}")

    def layering(name: str, rows: List[dict]) -> None:
        """rows: [{sym, dir, t, expired}] —— 与成交配对后按同向/反向分组。"""
        recs = []
        for f in fills:
            s, ft = f["symbol"], _dt(f["ts"])
            side = str((f.get("meta_json") or {}).get("side") or "").lower()
            if not (side.startswith("b") or side.startswith("s")):
                continue
            cand = [r for r in rows if r["sym"] == s and r["t"] <= ft.timestamp()]
            if not cand:
                continue
            c = max(cand, key=lambda r: r["t"])
            if c["dir"] == 0:
                continue
            sign = 1.0 if side.startswith("b") else -1.0
            recs.append({"ntl": float(f["notional"] or 0), "net_bp": float(f["net_bp"] or 0),
                         "same": sign * c["dir"] > 0, "expired": c.get("expired", False)})
        print(f"\n【{name} · ② 分层力 + ③ 实时性】")
        if not recs:
            print("  无可配对样本")
            return
        exp = sum(1 for r in recs if r["expired"])
        print(f"  可配对 {len(recs)} 笔，其中信号当时已过期 {exp} 笔（{100*exp/len(recs):.0f}%）")

        def agg(rows: List[dict]) -> str:
            if not rows:
                return "n=0"
            ntl = sum(r["ntl"] for r in rows) or 1e-9
            return (f"n={len(rows):<5} 净 "
                    f"{sum(r['net_bp']*r['ntl'] for r in rows)/ntl:>+7.3f}bp")
        print(f"  同向: {agg([r for r in recs if r['same']])}")
        print(f"  反向: {agg([r for r in recs if not r['same']])}")
        fresh = [r for r in recs if not r["expired"]]
        if fresh:
            print(f"  仅未过期: {agg(fresh)}   同向 {agg([r for r in fresh if r['same']])}")

    # ── A. 选币候选方向偏置 ──
    rows_a = _a("""SELECT symbol, direction_bias, confidence, score, created_at, valid_until
                   FROM coin_select_candidates
                   WHERE horizon='midlong' AND symbol = ANY(:syms) AND created_at >= :a
                   ORDER BY created_at""", syms=SYMS, a=since_dt.replace(tzinfo=None))
    print(f"\n=== A. 选币候选(midlong) 共 {len(rows_a)} 行 ===")
    ev_a = [{"sym": r["symbol"], "dir": DIRW.get(str(r["direction_bias"]).lower(), 0.0),
             "t": _dt(r["created_at"]).timestamp(), "meta": r}
            for r in rows_a if DIRW.get(str(r["direction_bias"]).lower(), 0.0) != 0.0]
    if ev_a:
        predictive("A 选币偏置", ev_a)
        lay_ev = [{"sym": e["sym"], "dir": e["dir"], "t": e["t"],
                   "expired": bool(e["meta"].get("valid_until")
                                   and _dt(e["meta"]["valid_until"]).timestamp() < e["t"] + 0)}
                  for e in ev_a]
        # 过期判定要在"成交时刻"比，交给 layering 内部用 valid_until：这里改成带 valid_until 的形式
        lay_rows = [{"sym": e["sym"], "dir": e["dir"], "t": e["t"],
                     "expired": False, "valid_until": e["meta"].get("valid_until")} for e in ev_a]
        # 复算过期：以每笔成交时刻对照该信号的 valid_until（naive → 本地）
        recs = []
        for f in fills:
            s, ft = f["symbol"], _dt(f["ts"])
            side = str((f.get("meta_json") or {}).get("side") or "").lower()
            if not (side.startswith("b") or side.startswith("s")):
                continue
            cand = [r for r in lay_rows if r["sym"] == s and r["t"] <= ft.timestamp()]
            if not cand:
                continue
            c = max(cand, key=lambda r: r["t"])
            if c["dir"] == 0:
                continue
            sign = 1.0 if side.startswith("b") else -1.0
            exp = bool(c["valid_until"] and _dt(c["valid_until"]) < ft)
            recs.append({"ntl": float(f["notional"] or 0), "net_bp": float(f["net_bp"] or 0),
                         "same": sign * c["dir"] > 0, "expired": exp})
        print(f"\n【A 选币偏置 · ② 分层力 + ③ 实时性】")
        print(f"  可配对 {len(recs)} 笔，信号当时已过期 {sum(1 for r in recs if r['expired'])} 笔")

        def agg(rows: List[dict]) -> str:
            if not rows:
                return "n=0"
            ntl = sum(r["ntl"] for r in rows) or 1e-9
            return (f"n={len(rows):<5} 净 "
                    f"{sum(r['net_bp']*r['ntl'] for r in rows)/ntl:>+7.3f}bp")
        print(f"  同向: {agg([r for r in recs if r['same']])}")
        print(f"  反向: {agg([r for r in recs if not r['same']])}")
        fresh = [r for r in recs if not r["expired"]]
        if fresh:
            print(f"  仅未过期: {agg(fresh)}   同向 {agg([r for r in fresh if r['same']])}")

    # ── B. signal_ledger ──
    rows_b = _a("""SELECT symbol, source, direction, strength, confidence, created_ms, expires_ms,
                          payload
                   FROM signal_ledger
                   WHERE symbol = ANY(:syms) AND created_ms >= :a ORDER BY created_ms""",
                syms=SYMS, a=int(since_dt.timestamp() * 1000))
    print(f"\n=== B. signal_ledger 共 {len(rows_b)} 行 ===")
    if rows_b:
        srcs: Dict[str, int] = {}
        for r in rows_b:
            srcs[str(r["source"])] = srcs.get(str(r["source"]), 0) + 1
        print(f"  按 source: {srcs}")
        ev_b = [{"sym": r["symbol"], "dir": float(r["direction"] or 0),
                 "t": float(r["created_ms"]) / 1000.0, "meta": r}
                for r in rows_b if float(r["direction"] or 0) != 0.0]
        if ev_b:
            predictive("B signal_ledger", ev_b)

            def _adj(r: dict) -> str:
                p = r.get("payload") or {}
                if isinstance(p, str):
                    import json
                    try:
                        p = json.loads(p)
                    except Exception:
                        p = {}
                for a in (p.get("position_adjustments") or []):
                    if isinstance(a, dict) and a.get("action"):
                        return str(a["action"])
                return ""

            recs = []
            adj_stat: Dict[str, List[float]] = {}
            for f in fills:
                s, ft = f["symbol"], _dt(f["ts"])
                side = str((f.get("meta_json") or {}).get("side") or "").lower()
                if not (side.startswith("b") or side.startswith("s")):
                    continue
                cand = [r for r in rows_b if r["symbol"] == s
                        and float(r["created_ms"]) / 1000.0 <= ft.timestamp()]
                if not cand:
                    continue
                c = max(cand, key=lambda r: float(r["created_ms"]))
                d = float(c["direction"] or 0)
                sign = 1.0 if side.startswith("b") else -1.0
                exp = bool(c.get("expires_ms")
                           and float(c["expires_ms"]) / 1000.0 < ft.timestamp())
                act = _adj(c)
                ntl = float(f["notional"] or 0)
                nb = float(f["net_bp"] or 0)
                if act:
                    adj_stat.setdefault(act, []).append(nb)
                if d != 0:
                    recs.append({"ntl": ntl, "net_bp": nb, "same": sign * d > 0, "expired": exp})

            def agg(rows: List[dict]) -> str:
                if not rows:
                    return "n=0"
                ntl = sum(r["ntl"] for r in rows) or 1e-9
                return (f"n={len(rows):<5} 净 "
                        f"{sum(r['net_bp']*r['ntl'] for r in rows)/ntl:>+7.3f}bp")
            print(f"\n【B signal_ledger · ② 分层力 + ③ 实时性】")
            print(f"  可配对 {len(recs)} 笔，信号当时已过期 {sum(1 for r in recs if r['expired'])} 笔")
            print(f"  同向: {agg([r for r in recs if r['same']])}")
            print(f"  反向: {agg([r for r in recs if not r['same']])}")
            if adj_stat:
                print("  payload.position_adjustments 动作 → 成交平均净 bp（按笔平均，非名义加权）：")
                for k, v in sorted(adj_stat.items(), key=lambda kv: -len(kv[1])):
                    print(f"      {k:<16} n={len(v):<5} 平均 {float(np.mean(v)):>+7.3f}bp")
    print("\n读法：与 F199/F202 同口径——同向/反向差异大 ⇒ 可当闸门；过期比例高 ⇒ 只能当慢变量 ✗")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
