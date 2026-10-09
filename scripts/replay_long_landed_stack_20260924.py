# -*- coding: utf-8 -*-
"""[2026-09-24 第6轮] **已落地机制的端到端逐笔验收**：长线「−8% 硬闸 + tick 级分档止盈 + 保本/锁利 + 结构止损」。

把本轮真正上线的那套逻辑（不是网格候选，而是**当前代码行为**）在 09-15 后全部长线仓上重放：
  ① −8% 硬闸：浮亏 ≥8%（价格口径）→ 全平（tick 级，`_run_v2_protection`）
  ② 分档止盈（tick 级）：8%→减 50%、15%→再减 50%、25%→清剩余；触发后 SL 推保本
  ③ 保本/锁利（E1 日任务 08:20）：峰值≥2%→SL=entry；≥3%→SL=entry×(1+peak/3)（市价上方时按引擎不变式跳过）
  ④ 结构止损（Chandelier，来自 exit_state_json）作为唯一底线，**只上移不下移**
  ⑤ 信号类平仓（非机械）仍按实际平仓时刻/价格封顶

输出：逐笔（实际 vs 机制重放）+ 汇总 + 触发计数。只读。
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
FEE_BP = 4.0
PEN_PP = 0.237
LADDER = ((8.0, 0.5), (15.0, 0.5), (25.0, 1.0))
LOSS_CAP = 8.0
MECH = {"sl", "breakeven_tp", "emergency_drawdown", "profit_drawdown_stage",
        "profit_drawdown_full", "profit_drawdown_hard", "trend_review_close",
        "staged_tp1", "staged_tp2", "staged_tp2_clear", "loss_cap_8pct"}


def is_mech(reason) -> bool:
    r = str(reason or "")
    return r in MECH or r.startswith("long_trend_v2:") or r.startswith("trend_e1:") or r.startswith("staged_tp")


def load_trades():
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, entry_price, size, opened_at, closed_at, close_price, close_reason,
                      coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0) pnl_usd,
                      exit_state_json, status, original_size
               from paper_positions
               where account_id=14 and timeframe_tier='long'
                 and opened_at > timestamp '2026-09-15 00:00:00'
               order by opened_at""")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for t in rows:
        t["struct_stop"] = None
        try:
            d = json.loads(t["exit_state_json"] or "{}")
            if d.get("structural_stop_price"):
                t["struct_stop"] = float(d["structural_stop_price"])
        except Exception:
            pass
    return rows


def next_e1(ts: int) -> int:
    t = dt.datetime.fromtimestamp(ts, CST)
    cand = t.replace(hour=8, minute=20, second=0, microsecond=0)
    if cand <= t:
        cand += dt.timedelta(days=1)
    return int(cand.timestamp())


def replay(t, bars, be_pct=2.0, lock_pct=3.0):
    entry = float(t["entry_price"])
    # [第6轮修] 用 original_size（原始仓位）：本脚本不建模"其它通道的分批减仓"，
    # 用当前 size 会把已实现部分漏掉 ⇒ 与实际不可比。仅当 original_size 缺失时回退。
    size0 = float(t.get("original_size") or t["size"] or 0)
    notional = entry * size0
    sl = float(t["struct_stop"]) if t["struct_stop"] else entry * 0.90
    rem = 1.0
    realized = 0.0
    fees = FEE_BP / 10000.0 * notional
    peak = 0.0
    done = set()
    events = []
    closed = t["closed_at"] is not None
    c_ep = int(t["closed_at"].replace(tzinfo=CST).timestamp()) if closed else None
    keep_signal = (not is_mech(t["close_reason"])) if closed else False
    next_lock_ts = next_e1(bars[0][0]) if bars else None
    exit_px, why = None, None

    for ts, _op, hi, lo, cl in bars:
        # ① 亏损硬闸（先看不利侧）
        cap_px = entry * (1 - LOSS_CAP / 100.0)
        if lo <= cap_px:
            realized += rem * notional * (cap_px - entry) / entry
            fees += FEE_BP / 10000.0 * notional * rem
            exit_px, why = cap_px, "loss_cap"
            events.append("loss_cap")
            break
        # ② 分档止盈（tick 级，用当根最高价判触发）
        for i, (sp, ratio) in enumerate(LADDER):
            if (i + 1) in done:
                continue
            thr = entry * (1 + sp / 100.0)
            if hi >= thr:
                cut = rem * ratio
                realized += cut * notional * (thr - entry) / entry
                fees += FEE_BP / 10000.0 * notional * cut
                rem -= cut
                done.add(i + 1)
                sl = max(sl, entry)          # 触发后推保本
                events.append("staged%d" % (i + 1))
                if rem <= 1e-9:
                    exit_px, why = thr, "staged_all"
                    break
        if exit_px is not None:
            break
        # ③ 保本/锁利（E1 日任务节奏：每天 08:20 评估一次）
        if next_lock_ts is not None and ts >= next_lock_ts:
            next_lock_ts = next_e1(ts + 1)
            cand_sl = None
            if peak >= lock_pct:
                cand_sl = entry * (1 + peak / 100.0 / 3.0)
            elif be_pct > 0 and peak >= be_pct:
                cand_sl = entry
            # 引擎"保护侧不变式"：SL 不得设在市价上方 ⇒ 高于现价则跳过
            if cand_sl is not None and cand_sl > sl and cand_sl < cl:
                sl = cand_sl
                events.append("lock_sl")
        # ④ 结构止损
        if lo <= sl:
            px = sl * (1 - PEN_PP / 100.0)
            realized += rem * notional * (px - entry) / entry
            fees += FEE_BP / 10000.0 * notional * rem
            exit_px, why = px, "sl"
            events.append("sl")
            break
        # ⑤ 信号类平仓封顶
        if keep_signal and c_ep is not None and ts >= c_ep:
            px = float(t["close_price"])
            realized += rem * notional * (px - entry) / entry
            fees += FEE_BP / 10000.0 * notional * rem
            exit_px, why = px, "signal_actual"
            break
        peak = max(peak, (hi - entry) / entry * 100.0)

    if exit_px is None:
        px = float(t["close_price"]) if closed else bars[-1][4]
        realized += rem * notional * (px - entry) / entry
        fees += FEE_BP / 10000.0 * notional * rem
        exit_px, why = px, "hold"
        if done and rem < 1.0 and not closed:
            events.append("partial_held")
        elif closed:
            events.append("signal_close" if keep_signal else "actual_close")
    pnl = realized - fees
    return {"id": t["id"], "sym": t["symbol"], "why": why, "pnl": pnl,
            "actual": float(t["pnl_usd"] or 0), "events": events, "peak": peak}


def main() -> int:
    trades = load_trades()
    now = int(dt.datetime.now(CST).timestamp())
    paths = {}
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for t in trades:
            o = int(t["opened_at"].replace(tzinfo=CST).timestamp())
            end = int(t["closed_at"].replace(tzinfo=CST).timestamp()) if t["closed_at"] else now
            cur.execute(
                """select timestamp, open_price, high_price, low_price, close_price from crypto_klines
                   where symbol=%s and exchange='binance' and period='1m' and environment='mainnet'
                     and timestamp between %s and %s order by timestamp""",
                (t["symbol"], o, end))
            paths[t["id"]] = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
                              for r in cur.fetchall()]
    usable = [t for t in trades if len(paths.get(t["id"]) or []) >= 5]
    act = sum(float(t["pnl_usd"] or 0) for t in usable)

    # ── A. 当前落地参数（保本 2% / 锁利 3%）逐笔 ──
    rows = [replay(t, paths[t["id"]], be_pct=2.0, lock_pct=3.0) for t in usable]
    print("样本: 09-15 后长线 %d 笔（含在仓）；实际合计 %+.2f USD" % (len(usable), act))
    print("  %-6s %-6s %-14s %10s %10s %10s  事件" % ("pos", "sym", "出场", "机制重放", "实际", "差"))
    for r in sorted(rows, key=lambda x: x["pnl"] - x["actual"]):
        print("  %-6s %-6s %-14s %+10.2f %+10.2f %+10.2f  %s"
              % (r["id"], r["sym"], r["why"], r["pnl"], r["actual"], r["pnl"] - r["actual"],
                 ",".join(r["events"])))
    tot = sum(r["pnl"] for r in rows)
    print("\n== A. 落地参数（保本2%/锁利3%）==")
    print("  合计 %+.2f USD vs 实际 %+.2f ⇒ 差 %+.2f（%d 笔）" % (tot, act, tot - act, len(rows)))
    print("  触发：分档 %d 笔 / 硬闸 %d 笔 / 锁利上移 %d 笔 / 结构止损 %d 笔"
          % (sum(1 for r in rows if any(e.startswith("staged") for e in r["events"])),
             sum(1 for r in rows if "loss_cap" in r["events"]),
             sum(1 for r in rows if "lock_sl" in r["events"]),
             sum(1 for r in rows if "sl" in r["events"])))

    # ── B. 保本阈值扫描（回答"保本线是不是太早"）──
    print("\n== B. 保本阈值扫描（锁利档固定 3%）==")
    print("  %-16s %-12s %-12s %s" % ("保本阈值", "合计USD", "vs 实际", "止损触发笔数"))
    for be in (0.0, 2.0, 3.0, 4.0, 5.0):
        rr = [replay(t, paths[t["id"]], be_pct=be, lock_pct=3.0) for t in usable]
        s = sum(x["pnl"] for x in rr)
        nsl = sum(1 for x in rr if "sl" in x["events"])
        print("  %-16s %+12.2f %+12.2f %d" % ("不保本" if be <= 0 else ("峰值≥%.0f%%→保本" % be),
                                             s, s - act, nsl))

    # ── C. 只算**已平仓**（去掉"浮动 vs 落袋"的口径偏向）──
    print("\n== C. 仅已平仓样本（去掉在仓浮动的口径偏向）==")
    closed_ids = {t["id"] for t in usable if t["closed_at"] is not None}
    rc = [r for r in rows if r["id"] in closed_ids]
    ac = sum(r["actual"] for r in rc)
    sc = sum(r["pnl"] for r in rc)
    print("  已平仓 n=%d：机制重放 %+.2f vs 实际 %+.2f ⇒ 差 %+.2f" % (len(rc), sc, ac, sc - ac))
    better = [r for r in rc if r["pnl"] > r["actual"]]
    worse = [r for r in rc if r["pnl"] < r["actual"]]
    print("  变好 %d 笔（合计 %+.2f）/ 变差 %d 笔（合计 %+.2f）"
          % (len(better), sum(r["pnl"] - r["actual"] for r in better),
             len(worse), sum(r["pnl"] - r["actual"] for r in worse)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
