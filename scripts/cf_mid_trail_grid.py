"""[2026-09-20] 中线**追踪止损规则网格**：规则 vs 规则，同一批路径（只读）。

与 `replay_mid_exit_counterfactual.py` 的区别（那一版基线 +$132 vs 实际 −$16.91，已作废）：
  * **不注入**实际信号出场（thesis_should_close/min_roi/trend_broken 等不可重放的东西一律不碰）；
  * **不发明**规则：只保留四件有代码依据的机械规则 —— 初始止损、追踪止损(激活/回撤)、止盈、时间上限；
  * 因此它的输出**不是 P&L 预测**，而是"同一批路径下，规则 A 换规则 B 的**差额**"。
    差额（delta）才是决策依据，绝对值不可信也不使用。

规则（保守口径：棒内先看不利用方向极值）：
  初始 SL   = entry × (1 ∓ eff_sl_pct)      eff_sl_pct 取该笔 `exit_state_json.exit_policy.sl_pct`
                                            （引擎自己按成交价算的，实测 1.62~1.81%）
  追踪       peak 用逐棒有利极值（不看未来）；peak ≥ act 时 SL ← entry×(1 ± (peak−cb))
  止盈       entry × (1 ± tp_pct)，tp_pct 默认 6.75%（= 落库 tp_price/entry − 1 的实测中位）
  时间上限   该笔 exit_policy.time_limit_sec（默认 172800s）
  穿透       −0.237pp（`audit_mid_exit_channels_20260920.py` A2 实测中位，成交比线更差）
  手续费     0.10pp/笔（占位，两边同扣，对 delta 影响小）

验收（先写死）：
  1) 两个独立价源（agg 15s 成交桶 / kline 1m）跑出的 **delta 方向必须一致**；
  2) 前 15 天 / 后 15 天两个半样本 **delta 方向必须一致**；
  3) 任何采纳的配置都必须让"最差单笔"不比基线差超过 eff_sl 上限（风险不许失控）。

用法：
  .venv\\Scripts\\python.exe scripts/cf_mid_trail_grid.py --days 30 --source agg
  .venv\\Scripts\\python.exe scripts/cf_mid_trail_grid.py --days 30 --source kline
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import statistics as st
import sys
from typing import Dict, List, Optional, Tuple

import psycopg

ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
ANALYTICS = "postgresql://laobao:alpha_pass@localhost:5432/alpha_analytics"
CST = dt.timezone(dt.timedelta(hours=8))
PEN_PP = 0.237      # 止损穿透（实测中位）
FEE_PP = 0.10


def load_trades(days: str) -> List[Dict]:
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, side, entry_price, size, opened_at, closed_at, close_price,
                      close_reason, exit_state_json, tp_price
               from paper_positions
               where account_id=14 and status='closed' and timeframe_tier='mid'
                 and closed_at > now() - (%s || ' days')::interval
               order by opened_at""",
            (days,),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    # 入场时的 regime（用于检验"放宽追踪只在趋势市里有效"这类 regime 依赖）
    # [2026-09-20] 该表 `timestamp` 的 epoch 单位在 s/ms/µs 之间不确定 ⇒ 一次性取近 800 行，
    # 在 Python 侧按数量级归一化后自己挑"入场前最近一行"，不在 SQL 里猜类型。
    try:
        with psycopg.connect(ANALYTICS, autocommit=True) as ac:
            acur = ac.cursor()
            cache: Dict[str, List] = {}
            for t in rows:
                sym = t["symbol"]
                if sym not in cache:
                    acur.execute(
                        """select regime_type, regime_direction, created_at from market_analysis_snapshots
                           where symbol=%s order by created_at""", (sym,))
                    series = []
                    for rt, rd, ca in acur.fetchall():
                        # 该表 `timestamp` 列的 epoch 单位在 s/ms/µs/ns 之间不明（试过三档都不对），
                        # 改用同表 `created_at`（真 timestamp）定位，稳定且可核验。
                        if hasattr(ca, "timestamp"):
                            v = (ca.replace(tzinfo=dt.timezone.utc) if ca.tzinfo is None
                                 else ca).timestamp()
                        else:
                            v = float(ca)
                            if v > 1e15:
                                v /= 1e6
                            elif v > 1e11:
                                v /= 1e3
                        series.append((v, rt, rd))
                    series.sort()
                    cache[sym] = series
                o_ep = int(t["opened_at"].replace(tzinfo=CST).timestamp())
                pick = None
                for v, rt, rd in cache[sym]:
                    if v <= o_ep + 8 * 3600:      # 容忍 8h 时区差
                        pick = (rt, rd)
                    else:
                        break
                t["regime_type"], t["regime_dir"] = pick if pick else (None, None)
    except Exception as e:  # noqa: BLE001
        print("  [warn] regime 查询失败（不影响主结论）: %s" % e)
        for t in rows:
            t.setdefault("regime_type", None)
            t.setdefault("regime_dir", None)
    return rows


def load_path(cur, symbol: str, exchange: str, t0: int, t1: int, source: str):
    if source == "agg":
        cur.execute(
            """select timestamp, high_price, low_price, vwap from market_trades_aggregated
               where symbol=%s and exchange=%s and timestamp between %s and %s order by timestamp""",
            (symbol, exchange, t0 * 1000, t1 * 1000),
        )
        return [(int(r[0]) // 1000, float(r[3] or r[1]), float(r[1]), float(r[2]), float(r[3] or r[1]))
                for r in cur.fetchall()]
    cur.execute(
        """select timestamp, open_price, high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange=%s and period='1m' and environment='mainnet'
             and timestamp between %s and %s order by timestamp""",
        (symbol, exchange, t0, t1),
    )
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in cur.fetchall()]


MECH_EXITS = {"sl", "tp", "breakeven_tp", "exit_policy:sl_pct", "exit_policy:trailing_callback",
              "staged_tp2_clear"}


def per_trade_params(t: Dict) -> Tuple[float, float, float, float, float]:
    """返回 (eff_sl_pct, tp_pct, time_limit_sec, act0, cb0)。

    [2026-09-20 按子代理规格修正] 追踪参数**必须逐笔读快照**：`EXIT_POLICY_MID_TRAILING_*` 现值是
    2.5/1.2（.env:1916-1917），但 9/17 之前开的仓快照里是 1.0/0.5，且 `EXIT_POLICY_REFRESH_OPEN`
    只在存量刷新时改写 —— 用全局现值当基线会把老仓的基线写错。
    另：`TIER_MID_BREAKEVEN_TP_PROGRESS=0.80` 是**占 TP 距离的比例**（需 +5.23%），
    所以 entry×(1+1.5%) 那条保本线在 mid 基本打不到，锁利线来自 `entry×(1+(peak−callback))`。
    """
    pol = {}
    try:
        pol = (json.loads(t["exit_state_json"] or "{}").get("exit_policy") or {})
    except Exception:
        pass
    sl = float(pol.get("sl_pct") or 1.7)
    tp = 6.75
    try:
        if t["tp_price"] and float(t["entry_price"]) > 0:
            tp = abs(float(t["tp_price"]) / float(t["entry_price"]) - 1.0) * 100.0
    except Exception:
        pass
    tl = float(pol.get("time_limit_sec") or 172800)
    act0 = pol.get("trailing_activation_pct")
    cb0 = pol.get("trailing_callback_pct")
    act0 = 2.5 if act0 is None else float(act0)
    cb0 = 1.2 if cb0 is None else float(cb0)
    return sl, tp, tl, act0, cb0


def simulate(t: Dict, bars, cfg: Dict) -> Dict:
    side = t["side"]
    long_ = str(side).lower().startswith("l")
    sign = 1.0 if long_ else -1.0
    entry = float(t["entry_price"])
    eff_sl, tp_pct, tl, act0, cb0 = per_trade_params(t)
    sl_pct = cfg["sl_mult"] * eff_sl if cfg.get("sl_mult") else (cfg.get("sl_abs") or eff_sl)
    sl = entry * (1 - sign * sl_pct / 100.0)
    tp = entry * (1 + sign * (cfg.get("tp_pct") or tp_pct) / 100.0)
    if cfg.get("use_snapshot"):
        act, cb = act0, cb0          # 基线 = 该笔自己的快照（9/17 前是 1.0/0.5，之后 2.5/1.2）
    else:
        act, cb = cfg.get("act"), cfg.get("cb")
    peak = 0.0
    o = int(t["opened_at"].replace(tzinfo=CST).timestamp())
    c_ep = int(t["closed_at"].replace(tzinfo=CST).timestamp())
    dead = o + tl
    # [2026-09-24 第13轮] 分档止盈支持（cfg["partial"] = [(触发%, 占当前仓比例), ...]）：
    # 中线声明的 ExitPolicy 档位 2.5/4/6% 此前**没有**被本网格建模；本块补上，
    # 每档触发按阈值价平掉 rem×ratio，并累计已实现（净额口径 = 已实现 + 剩余腿 - 费）。
    partial = list(cfg.get("partial") or [])
    partial_done = set()
    rem = 1.0
    realized_pp = 0.0
    # 该笔实际是"信号/策略"平掉的 ⇒ 引擎的其它判断保留：仍在该时刻平仓（除非新规则更早触发）。
    # 不这么做，delta 就会变成"如果当初不止损/不止盈一路持有"的牛市幻觉。
    keep_signal = cfg.get("cap_signal", True) and str(t["close_reason"] or "") not in MECH_EXITS
    exit_px, why = None, None
    for ts, _op, hi, lo, cl in bars:
        if ts > dead:
            exit_px, why = cl, "time_limit"
            break
        if act is not None and peak >= act:
            cand = entry * (1 + sign * (peak - cb) / 100.0)
            sl = max(sl, cand) if long_ else min(sl, cand)
        adv, fav = (lo, hi) if long_ else (hi, lo)
        if (adv <= sl) if long_ else (adv >= sl):
            exit_px = sl * (1 - sign * PEN_PP / 100.0)
            why = "trail" if (sl - entry) * sign > 0 else "sl"
            break
        if (fav >= tp) if long_ else (fav <= tp):
            exit_px, why = tp, "tp"
            break
        # 分档止盈（按不利侧先判、用有利极值触发的保守口径）
        if partial:
            for _i, (_thr, _ratio) in enumerate(partial):
                if _i in partial_done:
                    continue
                _px = entry * (1 + sign * _thr / 100.0)
                if (fav >= _px) if long_ else (fav <= _px):
                    _cut = rem * _ratio
                    realized_pp += _cut * _thr
                    rem -= _cut
                    partial_done.add(_i)
                    sl = max(sl, entry) if long_ else min(sl, entry)  # 触发后推保本
                    if rem <= 1e-9:
                        exit_px, why = _px, "staged_all"
                        break
            if exit_px is not None:
                break
        if keep_signal and ts >= c_ep:
            exit_px, why = float(t["close_price"]), "signal_actual"
            break
        ext = max(hi, cl) if long_ else min(lo, cl)
        r = ((ext - entry) / entry) * sign
        peak = max(peak, r * 100)
    if exit_px is None:
        exit_px, why = bars[-1][4], "data_end"
    gross = ((exit_px - entry) / entry) * sign * 100
    gross = realized_pp + rem * gross
    net = gross - FEE_PP
    notional = float(t["size"]) * entry
    return {"id": t["id"], "why": why, "net_pp": net, "gross_pp": gross,
            "usd": net / 100.0 * notional, "peak_pp": peak, "notional": notional,
            "opened_at": t["opened_at"]}


def stats(rows: List[Dict]) -> Dict:
    if not rows:
        return {"n": 0}
    nets = [r["net_pp"] for r in rows]
    wins = [x for x in nets if x > 0]
    return {"n": len(rows), "exp_pp": round(st.mean(nets), 4), "total_pp": round(sum(nets), 2),
            "total_usd": round(sum(r["usd"] for r in rows), 2),
            "win_rate": round(100 * len(wins) / len(nets), 1),
            "worst_pp": round(min(nets), 3), "best_pp": round(max(nets), 3),
            "median_hold_h": round(st.median([0.0]) if not rows else 0.0, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="30")
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--source", default="agg", choices=("agg", "kline"))
    ap.add_argument("--post-hours", type=float, default=72.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--cap-signal", type=int, default=1,
                    help="1=实际由信号/策略平掉的单，仍在该时刻平仓（默认）；0=关掉则变成'一路持有'幻觉")
    a = ap.parse_args(argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    trades = load_trades(a.days)
    paths: Dict[int, List] = {}
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for t in trades:
            o = int(t["opened_at"].replace(tzinfo=CST).timestamp())
            c = int(t["closed_at"].replace(tzinfo=CST).timestamp())
            end = max(c, o + 3600) + int(a.post_hours * 3600)
            paths[t["id"]] = load_path(cur, t["symbol"], a.exchange, o, end, a.source)
    usable = [t for t in trades if len(paths.get(t["id"]) or []) >= 5]
    print("样本: %d 笔 mid 已平仓 → 有足够路径 %d 笔（%s:%s，入场后最多追 %.0fh）"
          % (len(trades), len(usable), a.source, a.exchange, a.post_hours))

    cfgs = [
        {"name": "baseline 逐笔快照（当前状态）", "use_snapshot": True},
        {"name": "追踪 3.0/1.5", "act": 3.0, "cb": 1.5},
        {"name": "追踪 3.5/2.0", "act": 3.5, "cb": 2.0},
        {"name": "追踪 5.0/2.5", "act": 5.0, "cb": 2.5},
        {"name": "追踪 5.0/3.5", "act": 5.0, "cb": 3.5},
        {"name": "追踪 8.0/4.0", "act": 8.0, "cb": 4.0},
        {"name": "不追踪（只有初始SL/TP/时限）", "act": None, "cb": 1.2},
        {"name": "宽止损1.5×+追踪5.0/2.5", "act": 5.0, "cb": 2.5, "sl_mult": 1.5},
        {"name": "宽止损1.25×+追踪3.5/2.0", "act": 3.5, "cb": 2.0, "sl_mult": 1.25},
        {"name": "TP 10% + 追踪 5.0/2.5", "act": 5.0, "cb": 2.5, "tp_pct": 10.0},
        # [2026-09-24 第5轮] SL 口径专项：以**当前活体配置**（追踪 5.0/2.5）为基线，
        # 只动止损宽度（×快照 sl），回答"中线止损该多宽"。
        {"name": "活体基线(追5/2.5) SL×1.0", "act": 5.0, "cb": 2.5},
        {"name": "活体基线 SL×1.25", "act": 5.0, "cb": 2.5, "sl_mult": 1.25},
        {"name": "活体基线 SL×1.5", "act": 5.0, "cb": 2.5, "sl_mult": 1.5},
        {"name": "活体基线 SL×2.0", "act": 5.0, "cb": 2.5, "sl_mult": 2.0},
        {"name": "活体基线 SL×0.75(更紧)", "act": 5.0, "cb": 2.5, "sl_mult": 0.75},
        # [2026-09-24 第13轮] **分档止盈阶梯**专项（中线峰值中位仅 1.1%，声明的 2.5/4/6 很少触发）
        {"name": "L0 无分档(活体基线)", "act": 5.0, "cb": 2.5},
        {"name": "L1 声明档2.5/4/6×50/25/25", "act": 5.0, "cb": 2.5,
         "partial": [(2.5, 0.5), (4.0, 0.5), (6.0, 0.5)]},
        {"name": "L2 早档1.5/2.5/4×50/25/25", "act": 5.0, "cb": 2.5,
         "partial": [(1.5, 0.5), (2.5, 0.5), (4.0, 0.5)]},
        {"name": "L3 声明档2.5/4/6×33/33/34", "act": 5.0, "cb": 2.5,
         "partial": [(2.5, 0.3333), (4.0, 0.3333), (6.0, 0.5)]},
        {"name": "L4 单档1.5×50%", "act": 5.0, "cb": 2.5, "partial": [(1.5, 0.5)]},
        {"name": "L5 单档1.0×50%", "act": 5.0, "cb": 2.5, "partial": [(1.0, 0.5)]},
    ]
    half = len(usable) // 2
    base_rows = None
    out = {"source": a.source, "exchange": a.exchange, "n": len(usable), "configs": {}}
    print("  %-34s %-9s %-9s %-9s %-8s %-9s" % ("配置", "单笔期望", "总收益pp", "总USD", "胜率", "最差单笔"))
    for cfg in cfgs:
        cfg_out = dict(cfg)
        for c in cfgs:
            c["cap_signal"] = bool(a.cap_signal)
        rows = [simulate(t, paths[t["id"]], cfg) for t in usable]
        s = stats(rows)
        if cfg.get("use_snapshot"):
            base_rows = rows
        d_exp = None
        if base_rows is not None:
            d_exp = round(s["exp_pp"] - st.mean([r["net_pp"] for r in base_rows]), 4)
        fh = stats([simulate(t, paths[t["id"]], cfg) for t in usable[:half]])
        sh = stats([simulate(t, paths[t["id"]], cfg) for t in usable[half:]])
        out["configs"][cfg["name"]] = {**s, "delta_exp_pp_vs_baseline": d_exp,
                                       "exp_first_half": fh.get("exp_pp"),
                                       "exp_second_half": sh.get("exp_pp")}
        print("  %-34s %+-9.4f %+-9.2f %+-9.1f %-8s %-9s   Δ期望=%s  前半=%s 后半=%s"
              % (cfg["name"], s["exp_pp"], s["total_pp"], s["total_usd"], s["win_rate"], s["worst_pp"],
                 d_exp, fh.get("exp_pp"), sh.get("exp_pp")))
    # ── 保真度自检：基线模拟 vs 实际（这是"工具本身要验收"）──
    if base_rows is not None:
        by_id = {r["id"]: r for r in base_rows}
        diffs, capped, mechb = [], 0, 0
        for t in usable:
            r = by_id[t["id"]]
            sign = 1.0 if str(t["side"]).lower().startswith("l") else -1.0
            actual = ((float(t["close_price"]) - float(t["entry_price"])) / float(t["entry_price"])) * sign * 100
            diffs.append(abs(r["gross_pp"] - actual))
            if r["why"] == "signal_actual":
                capped += 1
            if str(t["close_reason"] or "") in MECH_EXITS:
                mechb += 1
        diffs.sort()
        print("  [保真度] 基线模拟 vs 实际: 中位|Δ|=%.3fpp  90分位=%.3fpp  ≤0.3pp 占比=%.0f%%"
              % (st.median(diffs), diffs[int(len(diffs) * 0.9)], 100 * sum(1 for d in diffs if d <= 0.3) / len(diffs)))
        print("           其中沿用实际信号出场 %d 笔 / 实际机械出场 %d 笔（机械笔由规则重新裁决，故 Δ 属预期）"
              % (capped, mechb))
        # ── regime 依赖检验：放宽规则是否只在趋势市有效？──
        reg: Dict[str, List[Dict]] = {}
        for t in usable:
            reg.setdefault(str(t.get("regime_type") or "unknown"), []).append(t)
        for label, sel in sorted(reg.items(), key=lambda kv: -len(kv[1])):
            if len(sel) < 15:
                continue
            bt = [by_id[t["id"]]["net_pp"] for t in sel]
            line = []
            for name in ("追踪 5.0/2.5", "宽止损1.5×+追踪5.0/2.5"):
                if name not in out["configs"]:
                    continue
                rows_sel = [simulate(t, paths[t["id"]], dict(next(c for c in cfgs if c["name"] == name),
                                                              cap_signal=bool(a.cap_signal))) for t in sel]
                d = st.mean([r["net_pp"] for r in rows_sel]) - st.mean(bt)
                line.append("%s Δ=%+.3f" % (name, d))
            print("  [regime=%-14s n=%-3d 基线期望=%+.4f] %s" % (label, len(sel), st.mean(bt), "  ".join(line)))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh2:
            json.dump(out, fh2, ensure_ascii=False, indent=1)
        print("写入 %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
