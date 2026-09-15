"""regime × 顺势/逆势 交叉检验：中长线的日线 regime 到底能不能"救"做市的成交质量。

[F200 2026-09-15] 为什么这一步决定"该共享什么"
--------------------------------------------------
已知（F199，2915 笔账本实证）：**顺势成交亏、逆势成交赚**，差 6.16bp：
    顺势（买在上涨/卖在下跌）−4.053bp   逆势（买在下跌/卖在上涨）+2.109bp
但这只是**无条件均值** ✓ —— 如果它在"趋势 regime"里会反过来，那结论就不能无条件使用 ✗✓。

中长线侧有一个判据最强、且有实证的日线 regime（`full_auto/midlong_circuit_gate._daily_regime`：
收盘 vs EMA200 且 60 日动量 ±5% ⇒ up/down/chop；其注释里记着 29 币 × 2018 天：
down regime 里做多 14 天 **−0.618%（t=−4.09）**、chop 期间 85 笔合计 **−53.66**）✓。

于是本脚本回答：
  ① 把每笔成交按"当时的日线 regime"分层后，顺势/逆势的优劣是否仍然成立？
  ② 如果 **chop 里逆势赚、趋势里顺势赚** ⇒ 日线 regime 就是**必须一起共享的条件** ✓✓
     （单独用分钟级趋势代理会在大行情里站反 ✗ —— 这正是用户说的"有时候方向不对容易大亏"✓）；
  ③ 如果分层后没有差异 ⇒ 日线 regime 对做市无增量价值 ✗，别接。

口径：regime 只用**成交之前**的日线数据（严格无未来函数 ✓），定义与 `_daily_regime` 完全一致 ✓。
日线数据来自 alpha_market.crypto_klines（表结构在运行时探测）。
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
TREND_WIN_S = 900.0


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


def daily_closes(symbol: str) -> List[Tuple[datetime, float]]:
    """取日线收盘（升序）。表结构探测：alpha_market.crypto_klines。"""
    cols = {c["column_name"] for c in _mrows(
        """SELECT column_name FROM information_schema.columns
           WHERE table_name='crypto_klines'""")}
    sym_col = next((c for c in ("symbol", "base_symbol", "asset") if c in cols), None)
    close_col = next((c for c in ("close", "close_price") if c in cols), None)
    t_col = next((c for c in ("open_time", "timestamp", "ts", "open_ts", "time") if c in cols), None)
    if not (sym_col and close_col and t_col):
        print(f"⚠ crypto_klines 列不识别: {sorted(cols)[:20]}")
        return []
    tf_col = next((c for c in ("timeframe", "interval", "tf", "period") if c in cols), None)
    where = f"{sym_col}=:s"
    if tf_col:
        where += f" AND {tf_col} IN ('1d','1D','d')"
    rows = _mrows(f"SELECT {t_col} AS t, {close_col} AS c FROM crypto_klines"
                  f" WHERE {where} ORDER BY {t_col}", s=symbol)
    out = []
    for r in rows:
        v = r["t"]
        if isinstance(v, (int, float)):
            ts = float(v)
            ts = ts / 1000.0 if ts > 1e11 else ts
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TZ)
        else:
            dt = _dt(v)
        if r["c"] is not None:
            out.append((dt, float(r["c"])))
    return out


def regime_at(series: List[Tuple[datetime, float]], when: datetime) -> str:
    """与 `_daily_regime` 完全一致：收盘>EMA200 且 60 日动量>+5% ⇒ up；反之 down；其余 chop。

    只用 `when` **之前**已收盘的日线（无未来函数 ✓）。
    """
    hist = [c for dt, c in series if dt < when]
    if len(hist) < 70:
        return ""
    n = len(hist)
    if n >= 200:
        k = 2.0 / 201.0
        ema = hist[0]
        for c in hist[1:]:
            ema = c * k + ema * (1 - k)
    else:
        ema = sum(hist) / n
    px = hist[-1]
    base = hist[-61] if n >= 61 else hist[0]
    mom60 = (px / base - 1.0) if base > 0 else 0.0
    if px > ema and mom60 > 0.05:
        return "up"
    if px < ema and mom60 < -0.05:
        return "down"
    return "chop"


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-09-14T11:50:00+08:00"
    since_dt = datetime.fromisoformat(since)
    fills = _rows("""SELECT ts, symbol, notional, net_bp, meta_json FROM lane_ledger
                     WHERE lane_id=:l AND event='fill' AND ts >= :a ORDER BY ts""",
                  l=LANE, a=since_dt)
    print(f"regime × 顺势/逆势 交叉检验 · {LANE} · 成交 {len(fills)} 笔 · 起点 {since}")
    if not fills:
        return 1

    t0 = min(_dt(f["ts"]) for f in fills).timestamp()
    t1 = max(_dt(f["ts"]) for f in fills).timestamp()
    lo, hi = int((t0 - TREND_WIN_S - 300) * 1000), int((t1 + 600) * 1000)
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

    dseries: Dict[str, List[Tuple[datetime, float]]] = {}
    for s in SYMS:
        dseries[s] = daily_closes(s)
        regs = {regime_at(dseries[s], _dt(f["ts"])) for f in fills if f["symbol"] == s}
        print(f"  日线 {s}: {len(dseries[s])} 根，regime 取值 {sorted(r for r in regs if r)}")

    recs = []
    for f in fills:
        s = f["symbol"]
        ta, ma = mids.get(s) or (np.array([]), np.array([]))
        if len(ta) < 10:
            continue
        side = str((f.get("meta_json") or {}).get("side") or "").lower()
        if not (side.startswith("b") or side.startswith("s")):
            continue
        t = _dt(f["ts"])
        ts = t.timestamp()
        j = int(np.searchsorted(ta, ts, "right")) - 1
        jp = int(np.searchsorted(ta, ts - TREND_WIN_S, "right")) - 1
        if j < 0 or jp < 0 or jp >= j or float(ma[j]) <= 0:
            continue
        trend = (float(ma[j]) - float(ma[jp])) / float(ma[jp]) * 1e4
        sign = 1.0 if side.startswith("b") else -1.0
        recs.append({"sym": s, "side": side, "trend": trend, "with": sign * trend > 0,
                     "regime": regime_at(dseries.get(s) or [], t),
                     "ntl": float(f["notional"] or 0), "net_bp": float(f["net_bp"] or 0)})
    print(f"可用 {len(recs)} 笔")

    def agg(rows: List[dict]) -> str:
        if not rows:
            return "n=0"
        ntl = sum(r["ntl"] for r in rows) or 1e-9
        return (f"n={len(rows):<5} 名义 ${ntl:>9,.0f}  净 "
                f"{sum(r['net_bp']*r['ntl'] for r in rows)/ntl:>+7.3f}bp")

    print("\n【按 regime 分层】")
    for reg in ("up", "down", "chop", ""):
        sub = [r for r in recs if r["regime"] == reg]
        if not sub:
            continue
        name = {"up": "上行 regime", "down": "下行 regime", "chop": "震荡 regime", "": "数据不足"}[reg]
        print(f"\n  {name}: {agg(sub)}")
        for label, sel in (("顺势", True), ("逆势", False)):
            s2 = [r for r in sub if r["with"] is sel]
            print(f"      {label}: {agg(s2)}")

    print("\n【核心问题：逆势在哪些 regime 里仍然赚钱？】")
    print("  若『震荡 regime 的逆势』最好、『趋势 regime 的顺势』最好 ⇒ 日线 regime 必须与分钟级代理"
          "一起用 ✓")
    print("  若各 regime 里都是逆势更好 ⇒ 分钟级代理已足够，日线 regime 无增量 ✗")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
