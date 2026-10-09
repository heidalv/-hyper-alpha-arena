"""[2026-09-20] 资金费**估算/回填预演**（默认只读 + 只写 CSV 到归档目录，不写业务库）。

口径（用户 2026-09-20 决定：三条车道全补、口径统一、回填已平仓、币安 taker 校准 0.05%）：
  * 结算网格 = **每 8 小时，UTC 00:00 / 08:00 / 16:00**（每日 3 次）；可配 `FUNDING_SETTLE_HOURS_UTC`。
  * 结算时刻的费率 = `alpha_market.perp_funding` 里**该时刻之前最近一条** `funding_rate`
    （该表是 5 分钟轮询的快照，`timestamp` 是毫秒轮询时刻，**不是**结算记录；超时容忍 6h）。
  * 名义 = `size × mark_price`（结算时刻的 mark；缺则用 `entry_price`）。
  * 方向：**多头付、空头收**（`funding_rate > 0` 时），杠杆不影响资金费。
  * 持仓跨结算时刻才算：`opened_at < t <= closed_at`；时间按 CST(持仓) → UTC(结算) 换算。
  * 交易所优先 asterdex，其次 binance（本仓默认所是 asterdex）。

用法：
  .venv\\Scripts\\python.exe scripts/estimate_position_funding_20260920.py --days 240 --out logs/_archive/20260920_funding/funding_estimate.csv
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import io
import os
import statistics as st
import sys
from typing import Dict, List, Tuple

import psycopg

ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
UTC = dt.timezone.utc
SETTLE_HOURS = tuple(int(x) for x in (os.getenv("FUNDING_SETTLE_HOURS_UTC") or "0,8,16").split(","))
TOL_H = 6.0
EXCH_PREF = ("asterdex", "binance")


def load_positions(days: str) -> List[Dict]:
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, account_id, symbol, side, size, entry_price, exchange,
                      opened_at, closed_at, timeframe_tier,
                      coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0) pnl_usd
               from paper_positions
               where status='closed' and opened_at is not null and closed_at is not null
                 and closed_at > now() - (%s || ' days')::interval
               order by opened_at""", (days,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_funding_series(cur, symbol: str, t0: int, t1: int) -> Dict[str, Tuple[List[int], List[float], List[float]]]:
    """按交易所取 [(created_at_epoch, funding_rate, mark_price)]，已排序，供 bisect。"""
    cur.execute(
        """select exchange, created_at, funding_rate, mark_price from perp_funding
           where symbol=%s and created_at between to_timestamp(%s) and to_timestamp(%s)
             and exchange = any(%s) order by created_at""",
        (symbol, t0, t1, list(EXCH_PREF)))
    out: Dict[str, Tuple[List[int], List[float], List[float]]] = {}
    for exch, ts, rate, mark in cur.fetchall():
        e = int(ts.timestamp())
        b = out.setdefault(exch, ([], [], []))
        b[0].append(e)
        b[1].append(float(rate or 0.0))
        b[2].append(float(mark or 0.0))
    return out


def settle_times(o_utc: dt.datetime, c_utc: dt.datetime) -> List[dt.datetime]:
    out = []
    day = o_utc.date()
    while True:
        for h in SETTLE_HOURS:
            t = dt.datetime.combine(day, dt.time(hour=h), tzinfo=UTC)
            if o_utc < t <= c_utc:
                out.append(t)
        day += dt.timedelta(days=1)
        if dt.datetime.combine(day, dt.time(0), tzinfo=UTC) > c_utc:
            break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="240")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    pos = load_positions(a.days)
    print("已平仓样本 %d 笔（近 %s 天）；结算网格 UTC %s（每日 %d 次）"
          % (len(pos), a.days, list(SETTLE_HOURS), len(SETTLE_HOURS)))
    rows, cache = [], {}
    miss_rate, miss_mark, no_settle = 0, 0, 0
    # [2026-09-20 修复] 费率序列必须按 symbol 的**全局跨度**加载一次：
    # 上一版按"每笔仓自己的时间窗"缓存，同一 symbol 的第 2..n 笔仓复用了第 1 笔的窗口，
    # 结算点全部落在序列外 ⇒ 1,209 个点漏取、长线资金费假显示为 0（该版结论已作废）。
    span: Dict[str, List[int]] = {}
    for p in pos:
        o = int(p["opened_at"].replace(tzinfo=CST).timestamp()) - 7200
        c = int(p["closed_at"].replace(tzinfo=CST).timestamp()) + 3600
        b = span.setdefault(p["symbol"], [o, c])
        b[0], b[1] = min(b[0], o), max(b[1], c)
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for sym, (lo, hi) in span.items():
            cache[sym] = load_funding_series(cur, sym, lo, hi)
        for p in pos:
            o_utc = p["opened_at"].replace(tzinfo=CST).astimezone(UTC)
            c_utc = p["closed_at"].replace(tzinfo=CST).astimezone(UTC)
            sts = settle_times(o_utc, c_utc)
            if not sts:
                no_settle += 1
            ser = cache.get(p["symbol"]) or {}
            series = None
            for ex in EXCH_PREF:
                if ex in ser and ser[ex][0]:
                    series = ser[ex]
                    break
            sign = 1.0 if str(p["side"]).lower().startswith("l") else -1.0
            total, n_hit = 0.0, 0
            for t in sts:
                te = int(t.timestamp())
                if series is None:
                    miss_rate += 1
                    continue
                j = bisect.bisect_right(series[0], te) - 1
                if j < 0 or (te - series[0][j]) > TOL_H * 3600:
                    miss_rate += 1
                    continue
                rate = series[1][j]
                mark = series[2][j]
                if mark <= 0:
                    mark = float(p["entry_price"])
                    miss_mark += 1
                total += float(p["size"]) * mark * rate * sign
                n_hit += 1
            rows.append({"id": p["id"], "acct": p["account_id"], "tier": p["timeframe_tier"],
                         "side": "long" if sign > 0 else "short", "symbol": p["symbol"],
                         "hold_h": round((c_utc - o_utc).total_seconds() / 3600, 2),
                         "settles": len(sts), "hit": n_hit,
                         "funding_usd": round(total, 4), "pnl_usd": round(float(p["pnl_usd"]), 2),
                         "net_usd": round(float(p["pnl_usd"]) - total, 2)})
    print("结算点总数命中率：%d 个点未取到费率；mark 缺失回退 %d 次；无结算点(持仓<8h或未跨点) %d 笔"
          % (miss_rate, miss_mark, no_settle))
    by: Dict[str, List[Dict]] = {}
    for r in rows:
        by.setdefault("%s/%s" % (r["tier"], r["side"]), []).append(r)
    print("  %-16s %-5s %-10s %-12s %-12s %s" % ("车道/方向", "n", "均持仓h", "资金费USD", "原PnL USD", "净(扣资金费)"))
    for k, sel in sorted(by.items(), key=lambda kv: sum(x["funding_usd"] for x in kv[1])):
        print("  %-16s %-5d %-10.1f %+-12.2f %+-12.2f %+.2f"
              % (k, len(sel), st.mean([x["hold_h"] for x in sel]),
                 sum(x["funding_usd"] for x in sel), sum(x["pnl_usd"] for x in sel),
                 sum(x["net_usd"] for x in sel)))
    print("  %-16s %-5d %-10s %+-12.2f %+-12.2f %+.2f"
          % ("合计", len(rows), "", sum(x["funding_usd"] for x in rows),
             sum(x["pnl_usd"] for x in rows), sum(x["net_usd"] for x in rows)))
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print("明细已写 %s（%d 行）" % (a.out, len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
