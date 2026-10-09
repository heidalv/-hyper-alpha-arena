# -*- coding: utf-8 -*-
"""[2026-09-24 第4轮] E1 日任务**执行滞后**量化：分档止盈的触发时点 vs 实际可得价格。

背景：E1 日任务 cron 08:20 每天一次，且决策用 `as_of_bar`（实测落后 2~3 天）。
刚落地的分档止盈（8/15/25%）触发在**日线收盘 + 市价双确认**下——
本脚本量化两件事：
  A. **迟到**：价格首次突破档位后，等到下一次 E1 跑（08:20）才减仓，价格差多少（滑点）；
  B. **错过**：盘中突破过档位、但日线收盘从未站上去的单（旧口径会整档漏掉）。

只读；样本 = 09-15 后全部长线仓（已平 + 在仓），1m K 线路径。
"""
from __future__ import annotations

import datetime as dt
import io
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
STAGES = (8.0, 15.0, 25.0)


def next_e1_run(ts_epoch: int) -> int:
    """下一次 E1 日任务（每天 08:20 CST）的时间戳。"""
    t = dt.datetime.fromtimestamp(ts_epoch, CST)
    cand = t.replace(hour=8, minute=20, second=0, microsecond=0)
    if cand <= t:
        cand += dt.timedelta(days=1)
    return int(cand.timestamp())


def main() -> int:
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, entry_price, size, opened_at, closed_at, status, close_price,
                      coalesce(unrealized_pnl,0) pnl, peak_pnl_pct
               from paper_positions
               where account_id=14 and timeframe_tier='long'
                 and (opened_at > timestamp '2026-09-15 00:00:00')
               order by opened_at""")
        cols = [d[0] for d in cur.description]
        trades = [dict(zip(cols, r)) for r in cur.fetchall()]

    now = int(dt.datetime.now(CST).timestamp())
    print("样本: 09-15 后长线仓 %d 笔（含在仓）" % len(trades))
    rows = []
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for t in trades:
            entry = float(t["entry_price"])
            notional = entry * float(t["size"] or 0)
            o = int(t["opened_at"].replace(tzinfo=CST).timestamp())
            end = int(t["closed_at"].replace(tzinfo=CST).timestamp()) if t["closed_at"] else now
            cur.execute(
                """select timestamp, high_price, close_price from crypto_klines
                   where symbol=%s and exchange='binance' and period='1m' and environment='mainnet'
                     and timestamp between %s and %s order by timestamp""",
                (t["symbol"], o, end))
            bars = [(int(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
            for sp in STAGES:
                thr = entry * (1 + sp / 100.0)
                hit = next((b for b in bars if b[1] >= thr), None)
                if not hit:
                    continue
                # 该档在"日线收盘"口径下是否也会触发（用 1d K 线）
                cur.execute(
                    """select max(close_price) from crypto_klines
                       where symbol=%s and exchange='binance' and period='1d' and environment='mainnet'
                         and timestamp between %s and %s""", (t["symbol"], o, end))
                max_daily_close = float(cur.fetchone()[0] or 0)
                daily_would = max_daily_close >= thr
                run_ts = next_e1_run(hit[0])
                # 下一次 E1 跑时的价格（取该时刻 1m 收盘）
                px_at_run = None
                for b in bars:
                    if b[0] >= run_ts:
                        px_at_run = b[2]
                        break
                if px_at_run is None:
                    px_at_run = bars[-1][2]
                slip_pct = (px_at_run - thr) / thr * 100.0
                rows.append({
                    "pos": t["id"], "sym": t["symbol"], "stage": sp, "thr": thr,
                    "hit_ts": hit[0], "px_at_hit": hit[2], "px_at_run": px_at_run,
                    "slip_pct": slip_pct, "slip_usd": slip_pct / 100.0 * notional,
                    "daily_would": daily_would, "notional": notional,
                })

    if not rows:
        print("样本内没有任何一笔触及 8/15/25% 档位。")
        return 0

    print("\n== 逐笔：档位触发 vs 实际执行（下一次 E1 跑）==")
    print("  %-6s %-6s %-5s %-10s %-10s %-8s %-9s %s" % ("pos", "sym", "档", "触发价", "E1跑时价", "滑点%", "滑点USD", "日线口径是否也触发"))
    for r in sorted(rows, key=lambda x: x["slip_pct"]):
        print("  %-6s %-6s %-5.0f%% %-10.4f %-10.4f %+8.2f %+9.2f %s" % (
            r["pos"], r["sym"], r["stage"], r["thr"], r["px_at_run"], r["slip_pct"], r["slip_usd"],
            "是" if r["daily_would"] else "**否（旧口径会整档漏掉）**"))
    slips = [r["slip_pct"] for r in rows]
    slip_usd = sum(r["slip_usd"] for r in rows)
    missed = [r for r in rows if not r["daily_would"]]
    print("\n== 汇总 ==")
    print("  触发次数（8/15/25 各档累计）: %d 笔次" % len(rows))
    print("  迟到滑点: 中位 %+.2f%% / 均值 %+.2f%%（负=等到 E1 跑时价格已低于触发价）"
          % (st.median(slips), st.mean(slips)))
    print("  迟到造成的美元差（按当时名义）: %+.2f USD" % slip_usd)
    print("  日线收盘口径会漏掉的档位触发: %d / %d（%.0f%%）"
          % (len(missed), len(rows), 100.0 * len(missed) / len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
