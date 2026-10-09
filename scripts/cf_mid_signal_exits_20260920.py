"""[2026-09-20] 中线**信号/主动平仓**通道归因（只读）。

背景：30 天里"信号/主动平仓"70 笔 = **−$298.29**（单笔 −1.151%），是最大的单一漏口（比 `sl` 通道还大 25%）。
本脚本不做全量规则回放（那需要重放 thesis/F39/no_progress 的判断，模型面太大、易错），
只做**不可能算错**的那一步：**平仓之后行情怎么走**。

判定口径（方向已调整；观察窗固定 24h，超出部分不可见，已在输出里写明）：
  post_MFE = 平仓后窗口内最大有利幅度（相对 entry）
  post_MAE = 平仓后窗口内最大不利幅度
  Δ(hold)   = 若不平、持有到窗口末 − 实际成交（pp），并折算 USD
  砍早了(premature) = post_MFE ≥ +1.0% 且 post_MAE ≥ −1.0%（之后既涨了、又没先大跌）
  砍对了(correct)   = post_MAE ≤ −1.0%（之后继续朝不利方向走）
  其余 = neutral

采纳纪律（与出场网格同一套）：两个价源 Δ 方向必须一致；只看单笔期望与子通道钱数，不据此直接改阈值。

用法：
  .venv\\Scripts\\python.exe scripts/cf_mid_signal_exits_20260920.py --source kline
  .venv\\Scripts\\python.exe scripts/cf_mid_signal_exits_20260920.py --source agg
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import statistics as st
import sys
from typing import Dict, List, Tuple

import psycopg

ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
MECH = {"sl", "tp", "breakeven_tp", "exit_policy:sl_pct", "exit_policy:trailing_callback",
        "staged_tp2_clear"}


def channel(reason: str) -> str:
    r = str(reason or "")
    if r in MECH or r in ("max_hold_timeout",):
        return "机械(不计入本表)"
    if r.startswith("midlong:no_progress"):
        return "midlong:no_progress"
    if r.startswith("midlong:bias_reversal"):
        return "midlong:bias_reversal"
    if r.startswith("trend_broken"):
        return "trend_broken*"
    if r.startswith("trend_weaken"):
        return "trend_weaken"
    if r.startswith("exit_policy:min_roi"):
        return "exit_policy:min_roi_decay"
    if r.startswith("profit_drawdown"):
        return "profit_drawdown*"
    if r.startswith("thesis_should_close"):
        return "thesis_should_close"
    if r.startswith("thesis_invalidation"):
        return "thesis_invalidation"
    if r.startswith("thesis_long_propagate"):
        return "thesis_long_propagate"
    if r.startswith("master_running_close"):
        return "master_running_close"
    if r.startswith("dust_cleanup"):
        return "dust_cleanup"
    return r.split(":")[0][:26] or "unknown"


def load(days: str) -> List[Dict]:
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, side, entry_price, size, opened_at, closed_at, close_price,
                      close_reason, coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0) usd
               from paper_positions
               where account_id=14 and status='closed' and timeframe_tier='mid'
                 and closed_at > now() - (%s || ' days')::interval
               order by opened_at""", (days,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def paths(cur, t: Dict, exchange: str, source: str, hours: float):
    cx = int(t["closed_at"].replace(tzinfo=CST).timestamp())
    t1 = int(cx + hours * 3600)
    if source == "agg":
        cur.execute(
            """select high_price, low_price, vwap from market_trades_aggregated
               where symbol=%s and exchange=%s and timestamp between %s and %s order by timestamp""",
            (t["symbol"], exchange, cx * 1000, t1 * 1000))
        return [(float(r[0]), float(r[1]), float(r[2] or r[0])) for r in cur.fetchall()]
    cur.execute(
        """select high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange=%s and period='1m' and environment='mainnet'
             and timestamp between %s and %s order by timestamp""",
        (t["symbol"], exchange, cx, t1))
    return [(float(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="30")
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--source", default="kline", choices=("agg", "kline"))
    ap.add_argument("--horizon-hours", type=float, default=24.0)
    a = ap.parse_args(argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    trades = [t for t in load(a.days) if channel(t["close_reason"]) != "机械(不计入本表)"]
    rows: List[Dict] = []
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for t in trades:
            bars = paths(cur, t, a.exchange, a.source, a.horizon_hours)
            if len(bars) < 3:
                continue
            long_ = str(t["side"]).lower().startswith("l")
            entry = float(t["entry_price"])
            def dr(px: float) -> float:
                r = (px - entry) / entry
                return r if long_ else -r
            fav = [(dr(hi) if long_ else dr(lo)) * 100 for hi, lo, _c in bars]
            adv = [(dr(lo) if long_ else dr(hi)) * 100 for hi, lo, _c in bars]
            last = dr(bars[-1][2]) * 100
            actual = dr(float(t["close_price"])) * 100
            rows.append({"ch": channel(t["close_reason"]), "pmfe": max(fav), "pmae": min(adv),
                         "delta": last - actual, "usd": float(t["usd"] or 0),
                         "notional": float(t["size"]) * entry,
                         "verdict": ("premature" if max(fav) >= 1.0 and min(adv) >= -1.0
                                     else ("correct" if min(adv) <= -1.0 else "neutral"))})
    print("信号/主动平仓样本 %d 笔（源=%s:%s，窗口=%.0fh；机械通道不计入）"
          % (len(rows), a.source, a.exchange, a.horizon_hours))
    print("  %-26s %-4s %-10s %-9s %-9s %-9s %-9s %s" %
          ("子通道", "n", "实际USD", "Δ_mean_pp", "Δ_total$", "postMFE", "postMAE", "early/correct/neutral"))
    by: Dict[str, List[Dict]] = {}
    for r in rows:
        by.setdefault(r["ch"], []).append(r)
    for ch, sel in sorted(by.items(), key=lambda kv: sum(x["usd"] for x in kv[1])):
        dusd = sum(x["delta"] / 100.0 * x["notional"] for x in sel)
        prem = sum(1 for x in sel if x["verdict"] == "premature")
        corr = sum(1 for x in sel if x["verdict"] == "correct")
        neu = len(sel) - prem - corr
        print("  %-26s %-4d %-10.2f %-9.3f %-9.1f %-9.2f %-9.2f %d/%d/%d" %
              (ch, len(sel), sum(x["usd"] for x in sel), st.mean([x["delta"] for x in sel]),
               dusd, st.median([x["pmfe"] for x in sel]), st.median([x["pmae"] for x in sel]),
               prem, corr, neu))
    dusd_all = sum(x["delta"] / 100.0 * x["notional"] for x in rows)
    print("  %-26s %-4d %-10.2f %-9.3f %-9.1f" % ("合计", len(rows), sum(x["usd"] for x in rows),
                                                  st.mean([x["delta"] for x in rows]), dusd_all))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
