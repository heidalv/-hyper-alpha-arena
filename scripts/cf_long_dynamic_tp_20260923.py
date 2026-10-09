# -*- coding: utf-8 -*-
"""[2026-09-23 深夜] 长线**动态止盈**准确分析（用户指定方向：分析准确的动态止盈，长线）。

准确性三件套（相对前几版网格的升级）：
  1. **动态 Chandelier**：不再用"入场时的静态结构止损"重放，而是按日重算
     `weekly_atr_causal` + `chandelier_long_stop`（与实盘同核函数），SL 只随 1d 收盘上移；
  2. **P0 保本/锁利**：峰值用 1m 路径精确累计，peak≥2% 推保本、≥3% 锁 1/3（实盘同款口径）；
  3. **保真度自检**：R0 基线（=实盘机制重放）对已平仓机械仓的出场价 vs 实际 close 的偏差分布
     必须可接受，否则本表不可信（先验收工具再看结论）。

规则族（动态止盈候选，全部只读）：
  R0 基线   Chandelier 2.0×ATR(1w) + P0保本/锁利            = 实盘现状
  R1 追踪   固定 3.0%/1.5%
  R2 追踪   固定 5.0%/2.5%
  R3 追踪   Chandelier 1.5×（更紧）
  R4 追踪   Chandelier 2.5×（更松）
  R5 追踪   peak − 1.0×ATR(1w)
  R6 分档   8/15/25 × 50/25/25 + 尾仓 Chandelier 2.0
  R7 分档   8/15/25 × 50/25/25 + 尾仓 peak−1.5×ATR(1w)

输出：R0 保真度表 → 规则网格（60d，Δvs实际/前后半/E1/最差）→ 4 开仓当前止盈线。
"""
from __future__ import annotations

import datetime as dt
import io
import json
import statistics as st
import sys

import psycopg

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.services.long_tier_manager import chandelier_long_stop, weekly_atr_causal  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
FEE_BP = 4.0
MECH_LONG = {"sl", "breakeven_tp", "emergency_drawdown", "profit_drawdown_stage",
             "profit_drawdown_full", "profit_drawdown_hard", "trend_review_close",
             "staged_tp1", "staged_tp2", "staged_tp2_clear"}


def is_mech(reason: str) -> bool:
    r = str(reason or "")
    return r in MECH_LONG or r.startswith("long_trend_v2:")


def load_trades(days: str):
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, side, entry_price, size, leverage, opened_at, closed_at,
                      close_price, close_reason, sl_price, tp_price, exit_state_json,
                      coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0) pnl_usd,
                      peak_unrealized_pnl
               from paper_positions
               where account_id=14 and timeframe_tier='long'
                 and (status in ('closed','liquidated') and closed_at > now() - (:d || ' days')::interval
                      or status='open' and id in (4712,4730,4743,4751))
               order by opened_at""", {"d": str(days)})
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for t in rows:
        t["is_open"] = t.get("closed_at") is None
        src = None
        try:
            d = json.loads(t["exit_state_json"] or "{}")
            src = d.get("entry_source") or (d.get("open_metadata") or {}).get("entry_source")
        except Exception:
            pass
        t["is_e1"] = str(src or "").strip().lower() == "trend_e1"
    return rows


def load_1d(sym, t0, t1):
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        cur.execute(
            """select timestamp, high_price, low_price, close_price from crypto_klines
               where symbol=%s and exchange='binance' and period='1d' and environment='mainnet'
                 and timestamp between %s and %s order by timestamp""", (sym, t0, t1))
        return [(int(r[0]), float(r[1]), float(r[2]), float(r[3])) for r in cur.fetchall()]


def load_1m(cur, sym, t0, t1):
    cur.execute(
        """select timestamp, open_price, high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange='binance' and period='1m' and environment='mainnet'
             and timestamp between %s and %s order by timestamp""", (sym, t0, t1))
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in cur.fetchall()]


def daily_index(daily_ts, ts):
    """最后一个 daily bar 时间戳 <= ts 的索引（-1 无）"""
    lo, hi = 0, len(daily_ts) - 1
    ans = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if daily_ts[mid] <= ts:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def replay(t, bars_1m, daily, rule):
    """daily: dict with keys ts(list), close(list), chand(list per mult), atr(list)"""
    long_ = str(t["side"]).lower().startswith("l")
    sign = 1.0 if long_ else -1.0
    entry = float(t["entry_price"])
    notional = entry * float(t["size"])
    keep_signal = not is_mech(t["close_reason"])
    c_ep = None if t["is_open"] else int(t["closed_at"].replace(tzinfo=CST).timestamp())
    mult = rule.get("mult", 2.0)
    chand = daily[f"chand_{mult}"]
    dts = daily["ts"]
    dclose = daily["close"]
    atr_s = daily["atr"]
    # 初始 SL = 入场日的 Chandelier 初始（实盘同口径；entry_idx 对应 bar）
    o_ep = int(t["opened_at"].replace(tzinfo=CST).timestamp())
    ei = daily_index(dts, o_ep)
    sl = float(chand[ei]) if (ei >= 0 and chand[ei] is not None) else entry * 0.95
    # 固定追踪/ATR 追踪/分档参数
    fx = rule.get("fix")            # (act_pct, cb_pct)
    atr_tr = rule.get("atr_trail")  # k：peak − k×ATR(1w)
    stages = rule.get("stages") or []
    fired = []
    rem = 1.0
    realized = 0.0
    fees = FEE_BP / 10000.0 * notional
    peak = 0.0
    last_day = -1
    exit_px, why = None, None

    for ts, _op, hi, lo, cl in bars_1m:
        # ── 每日决策（跨日时）──
        di = daily_index(dts, ts)
        if di > last_day and di >= ei:
            last_day = di
            # Chandelier 上移（只升不降）
            c_stop = float(chand[di] or 0) if di < len(chand) else 0.0
            if c_stop > sl:
                sl = c_stop
            # P0 保本/锁利（peak 无杠杆价格%）
            if peak >= 3.0:
                sl = max(sl, entry * (1.0 + peak / 100.0 / 3.0))
            elif peak >= 2.0:
                sl = max(sl, entry)
            # 分档止盈（按当日收盘）
            dc = float(dclose[di])
            for (sp, ratio) in list(stages):
                thr = entry * (1 + sign * sp / 100.0)
                hit = (dc >= thr) if long_ else (dc <= thr)
                if hit and sp not in fired:
                    fired.append(sp)
                    realized += rem * ratio * (sp / 100.0) * notional
                    fees += FEE_BP / 10000.0 * notional * rem * ratio
                    rem -= rem * ratio
                    sl = max(sl, entry)  # 每档触发后推保本
            if rem <= 1e-9:
                exit_px, why = dc, "staged_all"
                break
        # ── 固定%追踪线 ──
        if fx:
            act, cb = fx
            if peak >= act:
                cand = entry * (1 + sign * (peak - cb) / 100.0)
                sl = max(sl, cand) if long_ else min(sl, cand)
        # ── ATR 追踪线（每日用最新 ATR 重算）──
        if atr_tr and di >= ei:
            a = float(atr_s[di] or 0)
            if a > 0:
                cand = entry * (1 + sign * (peak - atr_tr * a / entry * 100.0) / 100.0) \
                    if False else entry * (1 + sign * peak / 100.0) - sign * atr_tr * a
                sl = max(sl, cand) if long_ else min(sl, cand)
        adv, fav = (lo, hi) if long_ else (hi, lo)
        if sl and ((adv <= sl) if long_ else (adv >= sl)):
            exit_px, why = sl * (1 - sign * 0.237 / 100.0), "sl"
            break
        if keep_signal and c_ep is not None and ts >= c_ep:
            exit_px, why = float(t["close_price"]), "signal_actual"
            break
        ext = max(hi, cl) if long_ else min(lo, cl)
        peak = max(peak, ((ext - entry) / entry) * sign * 100)
    if exit_px is None:
        exit_px = float(t.get("close_price") or bars_1m[-1][4])
        why = "hold"
    pnl = realized + rem * ((exit_px - entry) / entry) * sign * 100 / 100 * notional - fees
    return {"id": t["id"], "why": why, "pnl": pnl, "exit_px": exit_px,
            "peak_pct": peak, "sl_now": sl, "fired": fired, "rem": rem}


def main() -> int:
    trades = load_trades("60")
    closed = [t for t in trades if not t["is_open"]]
    open5 = [t for t in trades if t["is_open"]]
    e1n = sum(1 for t in closed if t["is_e1"])
    print("样本: long 60d 已平仓 %d（E1 %d/非E1 %d）+ 开仓 %d" % (len(closed), e1n, len(closed) - e1n, len(open5)))

    # 逐币取 1d 与 1m
    syms = sorted({t["symbol"] for t in trades})
    daily = {}
    paths = {}
    t_min = min(int(t["opened_at"].replace(tzinfo=CST).timestamp()) for t in trades)
    t_max = int(dt.datetime.now(CST).timestamp())
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for sym in syms:
            d1 = load_1d(sym, t_min - 90 * 86400, t_max)
            import pandas as pd
            df = pd.DataFrame({"high": [r[1] for r in d1], "low": [r[2] for r in d1],
                               "close": [r[3] for r in d1]})
            atr = weekly_atr_causal(df)
            d = {"ts": [r[0] for r in d1], "close": [r[3] for r in d1],
                 "atr": list(atr.fillna(0.0))}
            for m in (1.5, 2.0, 2.5):
                ch = chandelier_long_stop(df["close"].astype(float), atr, mult=m)
                d[f"chand_{m}"] = list(ch.fillna(0.0))
            daily[sym] = d
            for t in trades:
                if t["symbol"] != sym:
                    continue
                o = int(t["opened_at"].replace(tzinfo=CST).timestamp())
                c = c_end = (int(t["closed_at"].replace(tzinfo=CST).timestamp())
                             if not t["is_open"] else t_max)
                paths[t["id"]] = load_1m(cur, sym, o, max(c_end, o + 3600))

    rules = [
        {"name": "R0 基线 Chandelier2.0+保本/锁利(实盘现状)", "mult": 2.0},
        {"name": "R1 固定追踪3.0/1.5", "fix": (3.0, 1.5)},
        {"name": "R2 固定追踪5.0/2.5", "fix": (5.0, 2.5)},
        {"name": "R3 Chandelier1.5×", "mult": 1.5},
        {"name": "R4 Chandelier2.5×", "mult": 2.5},
        {"name": "R5 peak−1.0×ATR(1w)", "atr_trail": 1.0},
        {"name": "R6 分档8/15/25(50/25/25)+尾仓Ch2.0", "stages": [(8.0, 0.5), (15.0, 0.5), (25.0, 0.5)], "mult": 2.0},
        {"name": "R7 分档8/15/25+尾仓peak−1.5ATR", "stages": [(8.0, 0.5), (15.0, 0.5), (25.0, 0.5)], "atr_trail": 1.5},
    ]

    # ── R0 保真度自检（已平仓，机械仓的出场价 vs 实际 close）──
    print("\n== R0 保真度自检（机械仓 sim 出场价 vs 实际 close，价格差%）==")
    diffs = []
    for t in closed:
        if not is_mech(t["close_reason"]):
            continue
        r = replay(t, paths[t["id"]], daily[t["symbol"]], rules[0])
        actual = float(t["close_price"])
        diffs.append(abs(r["exit_px"] - actual) / actual * 100)
    if diffs:
        diffs.sort()
        print("  机械仓 n=%d  中位|Δ|%.2f%%  90分位%.2f%%" % (len(diffs), st.median(diffs), diffs[int(len(diffs)*0.9)]))
    else:
        print("  无机械仓")

    # ── 规则网格 ──
    print("\n== 规则网格（60d 已平仓，Δ = 规则总盈亏 − 实际总盈亏）==")
    print("  %-36s %-9s %-9s %-9s %-9s %-9s" % ("规则", "Δ总USD", "Δ均/笔", "前半Δ", "后半Δ", "E1组Δ"))
    actuals = [float(t["pnl_usd"]) for t in closed]
    half = len(closed) // 2
    for rule in rules:
        rows = [replay(t, paths[t["id"]], daily[t["symbol"]], rule) for t in closed]
        d_usd = sum(r["pnl"] for r in rows) - sum(actuals)
        d_mean = st.mean([r["pnl"] - a for r, a in zip(rows, actuals)])
        fh = sum(rows[i]["pnl"] - actuals[i] for i in range(half))
        sh = sum(rows[i]["pnl"] - actuals[i] for i in range(half, len(rows)))
        e1d = sum(rows[i]["pnl"] - actuals[i] for i, t in enumerate(closed) if t["is_e1"])
        print("  %-36s %+9.2f %+9.2f %+9.2f %+9.2f %+9.2f" % (rule["name"], d_usd, d_mean, fh, sh, e1d))

    # ── 4 开仓：当前止盈线 ──
    print("\n== 4 开仓按各规则『现在』的动态止盈线 / 若已触发则已落袋（USD）==")
    for rule in rules:
        line = "  %-36s" % rule["name"]
        for t in open5:
            r = replay(t, paths[t["id"]], daily[t["symbol"]], rule)
            now_float = float(t["pnl_usd"]) if t["is_open"] else None
            if r["why"] in ("sl", "staged_all", "signal_actual"):
                line += " | %-6s 已锁%+6.2f" % (t["symbol"], r["pnl"])
            else:
                line += " | %-6s 线%.4f 现%+5.2f" % (t["symbol"], r["sl_now"], now_float or 0)
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
