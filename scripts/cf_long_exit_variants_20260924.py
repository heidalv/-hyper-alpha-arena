# -*- coding: utf-8 -*-
"""[2026-09-24 第7轮] 长线出场**变体对照重放**：把"保本/锁利条件"的几种收紧闭法 vs 追踪类，放在同一台秤上。

背景（第6轮结论）：落地组合（分档 8/15/25 + 保本2%/锁利3% + −8%硬闸）在 09-15 后**已平仓 10 笔**上
比实际差 −$6.73（救回吐 2 笔 +$15.76 / 扫掉趋势 8 笔 −$22.49）；而网格里追踪类表现更好。
本轮把候选收紧闭法做成**同口径对照**（同一批路径、同一套结构止损、逐笔逐档），供选择；不改任何参数。

变体：
  V0 现状：分档8/15/25 + 保本2%/锁利3%(锁1/3) + 硬闸
  V1 只留分档 + 硬闸（完全不保本/不锁利）
  V2 分档 + 硬闸，且**只有第1档触发后**才允许抬止损
  V3 分档 + 硬闸 + 保本4%/锁利5%(锁1/3)
  V4 分档 + 硬闸 + 保本2%/锁利3% 但**锁峰值的一半**（peak/2）
  V5 全仓追踪 3.0/1.5 + 硬闸（网格最优类）
  V6 分档 + 余仓追踪 3.0/1.5 + 硬闸（D1b 类，锁利交给追踪）
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
        "staged_tp1", "staged_tp2", "staged_tp2_clear"}


def is_mech(reason) -> bool:
    r = str(reason or "")
    return r in MECH or r.startswith(("long_trend_v2:", "trend_e1:", "staged_tp"))


def next_e1(ts: int) -> int:
    t = dt.datetime.fromtimestamp(ts, CST)
    cand = t.replace(hour=8, minute=20, second=0, microsecond=0)
    return int((cand if cand > t else cand + dt.timedelta(days=1)).timestamp())


def load():
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, entry_price, size, original_size, opened_at, closed_at, close_price,
                      close_reason, coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0) pnl_usd,
                      exit_state_json
               from paper_positions
               where account_id=14 and timeframe_tier='long'
                 and opened_at > timestamp '2026-09-15 00:00:00' order by opened_at""")
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


def replay(t, bars, *, stages=LADDER, be_pct=2.0, lock_pct=3.0, lock_frac=3.0,
           lock_after_stage1=False, trail=None):
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
    nxt = next_e1(bars[0][0]) if bars else None
    exit_px = why = None
    trig = {"staged": 0, "cap": 0, "lock": 0, "trail": 0, "sl": 0}
    for ts, _o, hi, lo, cl in bars:
        cap_px = entry * (1 - LOSS_CAP / 100.0)
        if lo <= cap_px:
            realized += rem * notional * (cap_px - entry) / entry
            fees += FEE_BP / 10000.0 * notional * rem
            trig["cap"] += 1
            exit_px, why = cap_px, "loss_cap"
            break
        for i, (sp, ratio) in enumerate(stages):
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
                trig["staged"] += 1
                if rem <= 1e-9:
                    exit_px, why = thr, "staged_all"
                    break
        if exit_px is not None:
            break
        # 追踪线（活体网格里的规则：激活后 peak−cb）
        if trail:
            act, cb = trail
            if peak >= act:
                cand = entry * (1 + (peak - cb) / 100.0)
                if cand > sl:
                    sl = cand
                    trig["trail"] += 1
        # 保本/锁利（E1 日节奏）
        if nxt is not None and ts >= nxt:
            nxt = next_e1(ts + 1)
            allow = (not lock_after_stage1) or (1 in done)
            cand_sl = None
            if allow:
                if peak >= lock_pct:
                    cand_sl = entry * (1 + peak / 100.0 / lock_frac)
                elif be_pct > 0 and peak >= be_pct:
                    cand_sl = entry
            if cand_sl is not None and cand_sl > sl and cand_sl < cl:
                sl = cand_sl
                trig["lock"] += 1
        if lo <= sl:
            px = sl * (1 - PEN_PP / 100.0)
            realized += rem * notional * (px - entry) / entry
            fees += FEE_BP / 10000.0 * notional * rem
            trig["sl"] += 1
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
            "actual": float(t["pnl_usd"] or 0), "trig": trig, "why": why}


VARIANTS = [
    ("V0 现状(分档+保本2/锁利3)", dict()),
    ("V1 只留分档(不保本不锁利)", dict(be_pct=0.0, lock_pct=999.0)),
    ("V2 分档+仅第1档后才抬止损", dict(lock_after_stage1=True)),
    ("V3 保本4%/锁利5%", dict(be_pct=4.0, lock_pct=5.0)),
    ("V4 锁峰值一半(peak/2)", dict(lock_frac=2.0)),
    ("V5 全仓追踪3.0/1.5", dict(stages=(), be_pct=0.0, lock_pct=999.0, trail=(3.0, 1.5))),
    ("V6 分档+余仓追踪3.0/1.5", dict(be_pct=0.0, lock_pct=999.0, trail=(3.0, 1.5))),
]


def main() -> int:
    trades = load()
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
    closed = [t for t in usable if t["closed_at"] is not None]
    act_all = sum(float(t["pnl_usd"] or 0) for t in usable)
    act_cl = sum(float(t["pnl_usd"] or 0) for t in closed)
    print("样本：09-15 后长线 %d 笔（已平仓 %d）；实际 全样本 %+.2f / 已平仓 %+.2f"
          % (len(usable), len(closed), act_all, act_cl))
    print("\n  %-30s %11s %11s %11s %11s  %s"
          % ("变体", "全样本USD", "vs实际", "已平仓USD", "vs实际", "触发(分档/硬闸/锁利/追踪/止损)"))
    for name, kw in VARIANTS:
        rows = [replay(t, paths[t["id"]], **kw) for t in usable]
        s_all = sum(r["pnl"] for r in rows)
        s_cl = sum(r["pnl"] for r in rows if r["id"] in {t["id"] for t in closed})
        tg = [sum(r["trig"][k] for r in rows) for k in ("staged", "cap", "lock", "trail", "sl")]
        print("  %-30s %+11.2f %+11.2f %+11.2f %+11.2f  %s"
              % (name, s_all, s_all - act_all, s_cl, s_cl - act_cl, "/".join(str(x) for x in tg)))
    print("\n  （已平仓口径是主判据：在仓浮盈与'落袋'不可直接比）")

    # ── 已平仓样本的前后半稳健性（只对候选变体做）──
    print("\n== 已平仓样本前后半（按 opened_at 切）==")
    cl_ids = [t["id"] for t in closed]
    half = len(cl_ids) // 2
    h1, h2 = set(cl_ids[:half]), set(cl_ids[half:])
    for name, kw in VARIANTS:
        if name.startswith(("V1", "V2", "V3", "V5", "V6")):
            continue
        rows = [replay(t, paths[t["id"]], **kw) for t in closed]
        a1 = sum(r["actual"] for r in rows if r["id"] in h1)
        a2 = sum(r["actual"] for r in rows if r["id"] in h2)
        s1 = sum(r["pnl"] for r in rows if r["id"] in h1)
        s2 = sum(r["pnl"] for r in rows if r["id"] in h2)
        better = sum(1 for r in rows if r["pnl"] > r["actual"])
        print("  %-30s 前半 %+.2f vs %+.2f (差 %+.2f) | 后半 %+.2f vs %+.2f (差 %+.2f) | 变好 %d/%d 笔"
              % (name, s1, a1, s1 - a1, s2, a2, s2 - a2, better, len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
