# -*- coding: utf-8 -*-
"""多头入场新鲜度/择时毒性解析（第十七轮·深度二）。

第十六轮 learned 门（U2: up+chg24≥3%；C2: chop+pos≥60+chg≥2%）是在 bar 级
大样本上验证的。但历史真实入场的时点由 hub/主脑触发，可能滞后或超前于
「条件满足时刻」。本脚本量化三件事：

  1. **新鲜度分布**：每笔历史多头入场相对「learned 条件最近一次满足时刻」的
     延迟（小时）与超前（条件从未满足就入场=无动量接刀）；
  2. **择时毒性**：按新鲜度分桶的实际净 / 72h 前向；
  3. **hub 税**：同一符号在条件首次满足 bar 系统化入场（新出场结构）vs
     实际入场（新出场结构）的差额——hub 择时对多头是否同样有毒（空头侧
     第十五轮已证实对空头有毒）。

输出：`data/long_freshness.json`
用法：.venv\\Scripts\\python.exe backend/scripts/deep_long_freshness.py
"""
from __future__ import annotations

import bisect
import json
import os
import statistics as st
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
OUT = ROOT / "data" / "long_freshness.json"

FEE_SIDE = 0.0005
SLIP_SIDE = 0.0005
FUND_8H = 0.0001
LOOKBACK_BARS = 336  # 14 天


def cost_pct(h):
    return (FEE_SIDE + SLIP_SIDE) * 2 * 100 + (h / 8.0) * FUND_8H * 100


def load_longs():
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, entry_price, close_price, sl_price, original_size, size,
                   partial_realized_pnl, timeframe_tier, trade_nature, close_reason,
                   opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and side='long' and status='closed'
            order by opened_at
        """)).fetchall()]
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        st_rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, entry_price, exit_price as close_price, null as sl_price,
                   position_size as original_size, position_size as size,
                   null as partial_realized_pnl, null as timeframe_tier, null as trade_nature,
                   null as close_reason, opened_at, closed_at, decision_context, strategy_id
            from strategy_trades
            where side='long' and status='closed' and entry_price is not null
            order by opened_at desc limit 3000
        """)).fetchall()]
    have = {(r["symbol"], r["opened_at"]) for r in rows}
    for r in st_rows:
        if str(r["strategy_id"] or "").startswith("e2e_"):
            continue
        dc = r.get("decision_context")
        if isinstance(dc, str):
            try:
                dc = json.loads(dc)
            except Exception:
                try:
                    dc = eval(dc)  # noqa: S307
                except Exception:
                    dc = {}
        nature = (dc or {}).get("nature")
        if nature not in ("swing", "trend_follow", "position"):
            continue
        r["timeframe_tier"] = "mid" if nature == "swing" else "long"
        if (r["symbol"], r["opened_at"]) in have:
            continue
        have.add((r["symbol"], r["opened_at"]))
        rows.append(r)
    return rows


def load_klines(symbols):
    h1 = defaultdict(list)
    d1 = defaultdict(list)
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                h1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1d' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                d1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return h1, d1


def pick(series, sym):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 300:
            return v
    return None


def build_daily_regimes(dseries):
    """为 1d 序列逐根预计算 regime（与 _daily_regime 同口径，只用已收盘日线）。"""
    n = len(dseries)
    regs = [""] * n
    closes = [row[4] for row in dseries]
    for i in range(n):
        if i < 60:
            continue
        ema = sum(closes[max(0, i - 200): i + 1]) / min(i + 1, 200)
        px = closes[i]
        base = closes[i - 60]
        mom60 = (px / base - 1.0) if base > 0 else 0.0
        if px > ema and mom60 > 0.05:
            regs[i] = "up"
        elif px < ema and mom60 < -0.05:
            regs[i] = "down"
        else:
            regs[i] = "chop"
    return regs


def build_bar_features(h1_series, dseries):
    """逐 1h bar 预计算 (regime, pos24, chg24)。regime 用该 bar 之前的最后收盘日线。"""
    regs = build_daily_regimes(dseries)
    d_ts = [row[0] for row in dseries]
    closes = [row[4] for row in h1_series]
    highs = [row[2] for row in h1_series]
    lows = [row[3] for row in h1_series]
    n = len(h1_series)
    out_reg = [""] * n
    out_pos = [50.0] * n
    out_chg = [0.0] * n
    # 滚动 24h 高低（前 23 根 + 本根）
    hi_q = deque()
    lo_q = deque()
    for i in range(n):
        hi_q.append((highs[i], i))
        lo_q.append((lows[i], i))
        while hi_q and hi_q[0][1] < i - 23:
            hi_q.popleft()
        while lo_q and lo_q[0][1] < i - 23:
            lo_q.popleft()
        hi = max(x[0] for x in hi_q)
        lo = min(x[0] for x in lo_q)
        px = closes[i]
        out_pos[i] = (px - lo) / (hi - lo) * 100 if hi > lo else 50.0
        out_chg[i] = (px / closes[i - 24] - 1.0) * 100 if i >= 24 and closes[i - 24] > 0 else 0.0
        # 该 bar 之前最后收盘日线：bisect d_ts for ts of bar i，取前一根日线
        di = bisect.bisect_right(d_ts, h1_series[i][0]) - 1
        out_reg[i] = regs[di] if 0 <= di < len(regs) else ""
    return out_reg, out_pos, out_chg


def learned_ok(reg, pos, chg):
    """round-16 口径：up 需 chg≥3；chop 需 pos≥60 且 chg≥2；down 恒 False。
    （保留用于复现第十六轮；**分析/监控请用 `learned_ok_prod`**。）"""
    if reg == "up":
        return chg >= 3.0
    if reg == "chop":
        return pos >= 60.0 and chg >= 2.0
    return False


def learned_ok_v17(reg, pos, chg):
    """round-17 口径：up 需 chg∈[3,6)；**chop 无条件放行**；down 恒 False。

    ⚠️ **已废弃（仅用于复现第十七轮）**：round-17 的 chop 放宽在第十八轮被实际 P&L
    回滚（§28.6）。生产现行口径见 `learned_ok_prod`——两者在 chop 分支上不同，
    用错会让「门放行集」比生产实际更宽（chop 无条件纳入）。
    """
    if reg == "up":
        return 3.0 <= chg < 6.0
    if reg == "chop":
        return True
    return False


# 生产口径参数（与 `.env` / `midlong_circuit_gate._long_learned_ok` 默认值一致）
UP_CHG_MIN_PCT, UP_CHG_MAX_PCT = 3.0, 6.0
CHOP_POS_MIN_PCT, CHOP_CHG_MIN_PCT = 60.0, 2.0


def learned_ok_prod(reg, pos, chg):
    """**生产现行** learned 多头门（与 `midlong_circuit_gate._long_learned_ok` 同口径）。

    - regime=up  ：chg24 ∈ [+3%, +6%)
    - regime=chop：pos24 ≥ 60% 且 chg24 ≥ +2%（第十八轮回滚后的口径）
    - regime=down：恒 False（生产另有 down 硬拦，二者一致）

    注意：生产只对 `MIDLONG_LONG_LEARNED_TIERS`（当前 `mid`）施加该过滤；
    long 层仅受 down 硬拦。分析时对 long 层使用本函数即为**反事实**。
    """
    if reg == "up":
        return UP_CHG_MIN_PCT <= chg < UP_CHG_MAX_PCT
    if reg == "chop":
        return pos >= CHOP_POS_MIN_PCT and chg >= CHOP_CHG_MIN_PCT
    return False


def sim_new_exit(s, i, entry, sl_pct=6.0, trig=3.0, gap=1.5, max_h=168):
    peak = 0.0
    for k in range(i, min(i + int(max_h) + 1, len(s))):
        _ts, _o, h, l, c = s[k]
        hold = k - i
        mfe = (h - entry) / entry * 100
        peak = max(peak, mfe)
        if l <= entry * (1 - sl_pct / 100):
            return -sl_pct - cost_pct(hold), hold, "sl"
        if peak >= trig and mfe <= peak - gap:
            return max(peak - gap, 0) - cost_pct(hold), hold, "trail"
        if k == min(i + int(max_h), len(s) - 1):
            return (c - entry) / entry * 100 - cost_pct(hold), hold, "timeout"
    return None, 0, "no_data"


def main() -> int:
    rows = load_longs()
    h1, d1 = load_klines({r["symbol"] for r in rows})

    # 每 symbol 预计算特征
    feats = {}
    for sym in {r["symbol"] for r in rows}:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if s is None or ds is None or len(s) < 300 or len(ds) < 70:
            continue
        feats[sym] = (s, build_bar_features(s, ds))

    recs = []
    for r in rows:
        sym = r["symbol"]
        if sym not in feats:
            continue
        s, (reg_arr, pos_arr, chg_arr) = feats[sym]
        ts = int(r["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i < 25 or i >= len(s) - 2:
            continue
        entry = float(r["entry_price"] or 0)
        close = float(r["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        hold_h = (r["closed_at"] - r["opened_at"]).total_seconds() / 3600 if r["closed_at"] else 0
        actual = (close - entry) / entry * 100 - cost_pct(hold_h)
        k72 = min(i + 72, len(s) - 1)
        fwd72 = (s[k72][4] - entry) / entry * 100 - cost_pct(72)
        # 入场 bar 是否满足 learned 条件
        entry_ok = learned_ok(reg_arr[i], pos_arr[i], chg_arr[i])
        # 回溯找最近一次满足 bar
        last_ok = None
        first_ok = None
        for k in range(i - 1, max(0, i - LOOKBACK_BARS), -1):
            if learned_ok(reg_arr[k], pos_arr[k], chg_arr[k]):
                last_ok = k
                break
        for k in range(max(0, i - LOOKBACK_BARS), i):
            if learned_ok(reg_arr[k], pos_arr[k], chg_arr[k]):
                first_ok = k
                break
        if entry_ok:
            freshness_h = 0
            fresh_cat = "fresh(0h)"
        elif last_ok is not None:
            freshness_h = i - last_ok
            fresh_cat = "recent(1-12h)" if freshness_h <= 12 else (
                "stale(12-72h)" if freshness_h <= 72 else "stale(>72h)")
        else:
            freshness_h = None
            fresh_cat = "never(无动量入场)"
        # hub 税：条件首次满足 bar 系统化入场（新出场结构）
        sys_net = None
        if first_ok is not None:
            r_sys = sim_new_exit(s, first_ok, s[first_ok][4])
            sys_net = r_sys[0] if r_sys and r_sys[0] is not None else None
        act_new = sim_new_exit(s, i, entry)
        recs.append({
            "id": r["id"], "symbol": sym, "tier": str(r.get("timeframe_tier") or "?"),
            "opened": str(r["opened_at"])[:19],
            "entry_ok": entry_ok, "fresh_h": freshness_h, "fresh_cat": fresh_cat,
            "regime_at_entry": reg_arr[i], "chg24_at_entry": round(chg_arr[i], 2),
            "pos24_at_entry": round(pos_arr[i], 1),
            "actual": round(actual, 3), "fwd72": round(fwd72, 3),
            "sys_net": (round(sys_net, 3) if sys_net is not None else None),
            "act_new_net": (round(act_new[0], 3) if act_new[0] is not None else None),
            "hub_tax": (round(sys_net - act_new[0], 3)
                        if sys_net is not None and act_new[0] is not None else None),
        })

    print(f"可解析样本: {len(recs)}")

    def agg(rows_, label):
        if not rows_:
            return
        n = len(rows_)
        a = sum(x["actual"] for x in rows_) / n
        f72 = sum(x["fwd72"] for x in rows_) / n
        an = sum(x["act_new_net"] for x in rows_ if x["act_new_net"] is not None) / n
        tax = [x["hub_tax"] for x in rows_ if x["hub_tax"] is not None]
        tax_s = f"{sum(tax)/len(tax):+.2f}(n{len(tax)})" if tax else "-"
        print(f"  {label:<24} n={n:>3} 实际={a:>+8.2f}% 72h前向={f72:>+8.2f}% "
              f"实际入场+新出场={an:>+8.2f}% hub税={tax_s}")

    print("\n=== 总体 ===")
    agg(recs, "全部")
    print("\n=== 按入场时是否满足 learned 条件（闸门视角）===")
    agg([x for x in recs if x["entry_ok"]], "入场满足(闸会放行)")
    agg([x for x in recs if not x["entry_ok"]], "入场不满足(闸会拦)")
    print("\n=== 按新鲜度（条件最近满足时刻 vs 入场）===")
    for cat in ("fresh(0h)", "recent(1-12h)", "stale(12-72h)", "stale(>72h)", "never(无动量入场)"):
        agg([x for x in recs if x["fresh_cat"] == cat], cat)
    print("\n=== 按 tier ===")
    for tier in ("mid", "long"):
        agg([x for x in recs if x["tier"] == tier], f"tier={tier}")

    print("\n=== 逐币 hub 税（n≥3）===")
    bysym = defaultdict(list)
    for x in recs:
        bysym[x["symbol"]].append(x)
    for sym in sorted(bysym, key=lambda k: -len(bysym[k])):
        v = bysym[sym]
        tax = [x["hub_tax"] for x in v if x["hub_tax"] is not None]
        if len(tax) >= 3:
            print(f"  {sym:<8} n={len(tax):>3} hub税均值={sum(tax)/len(tax):>+8.2f}%")

    print("\n=== 总体 hub 税 ===")
    tax = [x["hub_tax"] for x in recs if x["hub_tax"] is not None]
    if tax:
        print(f"  n={len(tax)} 均值={sum(tax)/len(tax):+.3f}% 中位={st.median(tax):+.3f}% "
              f"正税(系统化更优)占比={sum(1 for t in tax if t > 0)/len(tax):.2f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": len(recs), "recs": recs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
