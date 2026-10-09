# -*- coding: utf-8 -*-
"""[2026-09-24] 长线/中线**每日出场对账器**（可反复跑；本轮的"逐笔盯触发"工具）。

三块输出：
  ① 在仓风险面板：每个长线仓的 −8% 硬闸触发价、锁利线（峰值≥3% 时 = entry×(1+peak/3)）、
     分档止盈下一档与触发价、当前 SL、结构止损、浮盈 —— 一眼看清"离触发还有多远"；
  ② 近期平仓逐笔：实际 vs "已落地机制"重放（V0：分档8/15/25 + 保本2%/锁利3% + −8%硬闸）；
  ③ 影子对账：影子日志里的虚拟追踪线 vs 实际平仓价（逐步累积成样本）。
只读。
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sys

import psycopg
from sqlalchemy import text as _sql_text

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
SHADOW = r"D:\001Alpha\Hyper-Alpha-Arena\data\long_exit_shadow.jsonl"
CST = dt.timezone(dt.timedelta(hours=8))
FEE_BP = 4.0
PEN_PP = 0.237
LADDER = ((8.0, 0.5), (15.0, 0.5), (25.0, 1.0))
LOSS_CAP = 8.0
MECH = {"sl", "breakeven_tp", "emergency_drawdown", "profit_drawdown_stage",
        "profit_drawdown_full", "profit_drawdown_hard", "trend_review_close",
        "staged_tp1", "staged_tp2", "staged_tp2_clear"}


def is_mech(r) -> bool:
    s = str(r or "")
    return s in MECH or s.startswith(("long_trend_v2:", "trend_e1:", "staged_tp"))


def next_e1(ts):
    t = dt.datetime.fromtimestamp(ts, CST)
    c = t.replace(hour=8, minute=20, second=0, microsecond=0)
    return int((c if c > t else c + dt.timedelta(days=1)).timestamp())


def section1_open_panel(db):
    print("=" * 108)
    print("① 在仓风险面板（长线）")
    rows = db.execute(_sql_text(
        """select id, symbol, entry_price, size, sl_price, unrealized_pnl, peak_unrealized_pnl,
                  peak_pnl_pct, mark_price, exit_state_json, opened_at
           from paper_positions where account_id=14 and status='open' and timeframe_tier='long'
           order by id""")).mappings().all()
    print("  %-6s %-6s %-10s %-8s %-8s %-10s %-12s %-12s %s"
          % ("pos", "sym", "现价", "浮盈$", "峰值%", "硬闸价(-8%)", "锁利线", "下一档触发", "SL/结构"))
    for r in rows:
        entry = float(r["entry_price"] or 0)
        mark = float(r["mark_price"] or 0)
        size = float(r["size"] or 0)
        peak = float(r["peak_pnl_pct"] or 0)
        cap_px = entry * (1 - LOSS_CAP / 100.0)
        lock_px = entry * (1 + peak / 3.0) if peak >= 0.03 else (entry if peak >= 0.02 else None)
        done = []
        struct = None
        try:
            d = json.loads(r["exit_state_json"] or "{}")
            struct = d.get("structural_stop_price")
            dd = d.get("staged_tp_done")
            if isinstance(dd, list):
                done = [int(x) for x in dd]
        except Exception:
            pass
        nxt = None
        for i, (sp, _ratio) in enumerate(LADDER):
            if (i + 1) not in done:
                nxt = (i + 1, sp, entry * (1 + sp / 100.0))
                break
        print("  %-6s %-6s %-10.4f %+8.2f %7.2f%% %-12.4f %-12s %-12s %s/%s"
              % (r["id"], r["symbol"], mark, float(r["unrealized_pnl"] or 0), peak * 100,
                 cap_px,
                 ("%.6f" % lock_px) if lock_px else "未触发(峰值<2%)",
                 ("第%d档 %s%% → %.6f" % nxt) if nxt else "三档已完成",
                 ("%.6f" % float(r["sl_price"])) if r["sl_price"] else "-",
                 ("%.4f" % float(struct)) if struct else "-"))
    print("  离硬闸距离（现价→−8% 价，越小越危险）：", end="")
    parts = []
    for r in rows:
        entry = float(r["entry_price"] or 0)
        mark = float(r["mark_price"] or 0)
        if mark > 0 and entry > 0:
            parts.append("%s %.2f%%" % (r["symbol"], (mark - entry * (1 - LOSS_CAP / 100.0)) / mark * 100))
    print("  ".join(parts) if parts else "无")
    print("  分档止盈已触发：", end="")
    done_any = []
    for r in rows:
        try:
            d = json.loads(r["exit_state_json"] or "{}")
            if d.get("staged_tp_done"):
                done_any.append("%s%s" % (r["symbol"], d["staged_tp_done"]))
        except Exception:
            pass
    print(", ".join(done_any) if done_any else "（无）")


def replay_v0(t, bars):
    entry = float(t["entry_price"])
    size0 = float(t.get("original_size") or t["size"] or 0)
    notional = entry * size0
    sl = float(t["struct_stop"]) if t["struct_stop"] else entry * 0.90
    rem, realized = 1.0, 0.0
    fees = FEE_BP / 10000.0 * notional
    peak, done = 0.0, set()
    closed = t["closed_at"] is not None
    c_ep = int(t["closed_at"].replace(tzinfo=CST).timestamp()) if closed else None
    keep_signal = (not is_mech(t["close_reason"])) if closed else False
    nxt = next_e1(bars[0][0])
    exit_px = why = None
    for ts, _o, hi, lo, cl in bars:
        cap_px = entry * (1 - LOSS_CAP / 100.0)
        if lo <= cap_px:
            realized += rem * notional * (cap_px - entry) / entry
            fees += FEE_BP / 10000.0 * notional * rem
            exit_px, why = cap_px, "loss_cap"
            break
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
                sl = max(sl, entry)
                if rem <= 1e-9:
                    exit_px, why = thr, "staged_all"
                    break
        if exit_px is not None:
            break
        if ts >= nxt:
            nxt = next_e1(ts + 1)
            cand = entry * (1 + peak / 100.0 / 3.0) if peak >= 3.0 else (entry if peak >= 2.0 else None)
            if cand is not None and cand > sl and cand < cl:
                sl = cand
        if lo <= sl:
            px = sl * (1 - PEN_PP / 100.0)
            realized += rem * notional * (px - entry) / entry
            fees += FEE_BP / 10000.0 * notional * rem
            exit_px, why = px, "sl"
            break
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
    return {"id": t["id"], "sym": t["symbol"], "pnl": realized - fees,
            "actual": float(t["pnl_usd"] or 0), "why": why}


def section2_recent_closes(db):
    print("\n" + "=" * 108)
    print("② 近 48h 已平仓逐笔：实际 vs 已落地机制(V0)重放")
    rows = db.execute(_sql_text(
        """select id, symbol, entry_price, size, original_size, opened_at, closed_at, close_price,
                  close_reason, coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0) pnl_usd,
                  exit_state_json
           from paper_positions
           where account_id=14 and timeframe_tier='long' and status in ('closed','liquidated')
             and closed_at > now() - interval '48 hours' order by closed_at""")).mappings().all()
    if not rows:
        print("  （近 48h 无长线平仓）")
        return
    out = []
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for r in rows:
            t = dict(r)
            t["struct_stop"] = None
            try:
                d = json.loads(t["exit_state_json"] or "{}")
                if d.get("structural_stop_price"):
                    t["struct_stop"] = float(d["structural_stop_price"])
            except Exception:
                pass
            o = int(t["opened_at"].replace(tzinfo=CST).timestamp())
            c = int(t["closed_at"].replace(tzinfo=CST).timestamp())
            cur.execute(
                """select timestamp, open_price, high_price, low_price, close_price from crypto_klines
                   where symbol=%s and exchange='binance' and period='1m' and environment='mainnet'
                     and timestamp between %s and %s order by timestamp""", (t["symbol"], o, c))
            bars = [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in cur.fetchall()]
            if len(bars) >= 5:
                out.append(replay_v0(t, bars))
    print("  %-6s %-6s %-12s %10s %10s %10s" % ("pos", "sym", "机制出场", "机制重放", "实际", "差"))
    tm = ta = 0.0
    for r in out:
        tm += r["pnl"]; ta += r["actual"]
        print("  %-6s %-6s %-12s %+10.2f %+10.2f %+10.2f" % (r["id"], r["sym"], r["why"], r["pnl"], r["actual"], r["pnl"] - r["actual"]))
    print("  合计：机制 %+.2f vs 实际 %+.2f ⇒ 差 %+.2f（%d 笔）" % (tm, ta, tm - ta, len(out)))


def section3_shadow():
    print("\n" + "=" * 108)
    print("③ 影子对账（虚拟追踪线 vs 实际平仓）")
    closes, ticks = [], 0
    try:
        with open(SHADOW, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("kind") == "close":
                    closes.append(d)
                else:
                    ticks += 1
    except FileNotFoundError:
        print("  影子日志不存在")
        return
    print("  累计 tick=%d，平仓样本=%d（转正门槛 30 笔）" % (ticks, len(closes)))
    for c in closes:
        virt = str(c.get("virtual_at_close") or "")
        import re
        m = re.search(r"mark=([0-9.]+)", virt)
        px = float(m.group(1)) if m else None
        entry = float(c.get("entry") or 0)
        close_px = float(c.get("close_price") or 0)
        if px and entry > 0:
            print("  pos=%s %-6s 实际平仓 %.4f (%.2f%%) | 影子线时 mark %.4f (%.2f%%) | 差 %.2f%%"
                  % (c.get("position_id"), c.get("symbol"), close_px, (close_px - entry) / entry * 100,
                     px, (px - entry) / entry * 100, (px - close_px) / entry * 100))


def main() -> int:
    db = __import__("backend.database.connection", fromlist=["SessionLocal"]).SessionLocal()
    try:
        db.execute(_sql_text("SET app.is_admin='on'"))
        print("对账时间：%s" % dt.datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST"))
        section1_open_panel(db)
        section2_recent_closes(db)
        section3_shadow()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    import os
    sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
    from dotenv import load_dotenv
    load_dotenv(r"D:\001Alpha\Hyper-Alpha-Arena\.env", override=False)
    raise SystemExit(main())
