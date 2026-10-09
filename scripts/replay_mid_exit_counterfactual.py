"""[2026-09-20] 中线出场结构 **反事实重放**（只读脚本：不改任何参数、不写业务表）。

为什么要它：`_中长线负期望根因报告_20260909.md` 与 2026-09-20 的止盈止损根因排查都指向
"出场结构"，但两处引用的反事实互相矛盾（代码注释说 mid SL cap 2% 能 +73.54；`exit_policy`
的 30 天 MFE/MAE 网格说放宽止损无用）。所以必须**在同一份数据、同一套规则下自己重放一遍**。

三段式（缺一不可，前一段不过就不许看下一段结论）：

  阶段1 数据对齐验收 —— 用 `alpha_market.crypto_klines` 1m K 线重算每笔的 MFE/MAE，
        与 `paper_positions.peak_pnl_pct / trough_pnl_pct` 对比。K 线是 UTC epoch，
        持仓时间是 CST 墙上时间；若时区/交易所口径错，这一步会当场炸掉（所以它既是验收，
        也用来在 binance / asterdex / hyperliquid 之间**用数据选价源**，而不是猜）。

  阶段2 现规则复现 —— 对机械出场的单（sl / breakeven_tp / tp / exit_policy:sl_pct），
        用 DB 里记录的实际触发线在 K 线上找"首次触及"的分钟，与实际 `closed_at` 比。
        这验证的是"当前参数到底是什么"（initial SL 是不是 entry×0.985、追踪是不是 peak−callback），
        同时量出**穿透/滑点**（实际成交价比触发线差多少），反事实里照此扣掉，避免算乐观。

  阶段3 反事实网格 + 稳健性 —— 机械出场叠加在**实际信号出场路径**之上（信号类出场
        thesis_should_close / min_roi_decay / trend_broken 等无法重放，就沿用实际时间与价格），
        只改机械规则参数。每个配置报：总收益、单笔期望、胜率、盈亏比、最大单笔亏损，
        并做 **对半切（前15天/后15天）** 与 **留一币（drop-one-symbol）** 稳健性检验。

采纳规则（**先写死再看结果**，防止事后挑参数）：
  单笔净期望 > 基线，且 前后两半都 > 基线，且 留一币后仍 > 基线，
  且 总净收益 > 基线，且 最大单笔亏损不超过基线设计上限×1.2，且 样本数 ≥ 140。

用法：
  .venv\\Scripts\\python.exe scripts/replay_mid_exit_counterfactual.py --stage validate
  .venv\\Scripts\\python.exe scripts/replay_mid_exit_counterfactual.py --stage grid --exchange binance
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics as st
import sys
from typing import Dict, List, Optional, Tuple

import psycopg

ARENA = os.getenv("ARENA_DSN", "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET = os.getenv("MARKET_DSN", "postgresql://laobao:alpha_pass@localhost:5432/alpha_market")
CST = dt.timezone(dt.timedelta(hours=8))
EXCHANGES = ("binance", "asterdex", "hyperliquid", "bybit", "okx")


# ─────────────────────────── 数据 ───────────────────────────

def load_trades(days: int, account: int) -> List[Dict]:
    sql = """
        select id, symbol, side, entry_price, size, leverage, margin,
               opened_at, closed_at, close_price, close_reason,
               peak_pnl_pct, trough_pnl_pct, sl_price, tp_price,
               coalesce(partial_realized_pnl, 0) + coalesce(unrealized_pnl, 0) as pnl_usd,
               coalesce(partial_fee_paid, 0) as fee_usd, exit_state_json
        from paper_positions
        where account_id = %s and status = 'closed' and timeframe_tier = 'mid'
          and closed_at > now() - (%s || ' days')::interval
          and opened_at is not null and closed_at is not null
        order by opened_at
    """
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(sql, (account, days))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_bars(cur, symbol: str, exchange: str, t0: int, t1: int) -> List[Tuple[int, float, float, float, float]]:
    cur.execute(
        """select timestamp, open_price, high_price, low_price, close_price
           from crypto_klines
           where symbol = %s and exchange = %s and period = '1m' and environment = 'mainnet'
             and timestamp between %s and %s
           order by timestamp""",
        (symbol, exchange, t0, t1),
    )
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in cur.fetchall()]


def load_bars_agg(cur, symbol: str, exchange: str, t0: int, t1: int) -> List[Tuple[int, float, float, float, float]]:
    """15s 成交聚合桶（真实成交的 high/low；open/close 用 vwap 近似——回放里只用于
    "时间上限出场价"和"用收盘价更新峰值"，近似不影响触发判定）。"""
    cur.execute(
        """select timestamp, high_price, low_price, vwap
           from market_trades_aggregated
           where symbol = %s and exchange = %s and timestamp between %s and %s
           order by timestamp""",
        (symbol, exchange, t0 * 1000, t1 * 1000),
    )
    out = []
    for ts_ms, hi, lo, vw in cur.fetchall():
        v = float(vw or hi)
        out.append((int(ts_ms) // 1000, v, float(hi), float(lo), v))
    return out


def bars_loader(source: str):
    return load_bars_agg if source == "agg" else load_bars


def epoch_of(naive: dt.datetime, tz: dt.timezone) -> int:
    return int(naive.replace(tzinfo=tz).timestamp())


def dir_ret(side: str, entry: float, price: float) -> float:
    r = (price - entry) / entry
    return r if str(side).lower().startswith("l") else -r


def mfe_mae(bars, side: str, entry: float) -> Tuple[float, float]:
    """返回 (MFE%, MAE%)，方向已调整（MAE 为负）。"""
    if not bars:
        return 0.0, 0.0
    rets = [dir_ret(side, entry, b[2] if str(side).lower().startswith("l") else b[3]) for b in bars]
    closes = [dir_ret(side, entry, b[4]) for b in bars]
    return max(rets + closes) * 100, min(rets + closes) * 100


# ─────────────────────────── 阶段1：对齐验收 ───────────────────────────

def stage_validate(trades: List[Dict], days: int, post_h: float, limit: Optional[int]) -> Dict:
    sample = trades if not limit else trades[-limit:]
    report: Dict[str, Dict] = {}
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        combos = [("agg", "binance"), ("agg", "asterdex"), ("agg", "hyperliquid"),
                  ("kline", "binance"), ("kline", "asterdex"), ("kline", "hyperliquid")]
        for source, exch in combos:
            loader = bars_loader(source)
            for tzname, tz in (("CST", CST), ("UTC", dt.timezone.utc)):
                dpeak, dtrough, n_ok_peak, n_ok_trough, n_ok10p, n_ok10t, n_used, n_bars = [], [], 0, 0, 0, 0, 0, 0
                for t in sample:
                    o = epoch_of(t["opened_at"], tz)
                    c = epoch_of(t["closed_at"], tz)
                    bars = loader(cur, t["symbol"], exch, o, int(c + post_h * 3600))
                    if len(bars) < 3:
                        continue
                    n_used += 1
                    n_bars += len(bars)
                    mfe, mae = mfe_mae(bars, t["side"], float(t["entry_price"]))
                    pk = float(t["peak_pnl_pct"] or 0.0) * 100
                    tr = float(t["trough_pnl_pct"] or 0.0) * 100
                    dpeak.append(mfe - pk)
                    dtrough.append(mae - tr)
                    if abs(mfe - pk) <= 0.15:
                        n_ok_peak += 1
                    if abs(mae - tr) <= 0.15:
                        n_ok_trough += 1
                    if abs(mfe - pk) <= 0.10:
                        n_ok10p += 1
                    if abs(mae - tr) <= 0.10:
                        n_ok10t += 1
                key = f"{source}:{exch}|{tzname}"
                report[key] = {
                    "n_used": n_used, "avg_bars": round(n_bars / n_used, 1) if n_used else 0,
                    "peak_within_0.15pp": n_ok_peak, "trough_within_0.15pp": n_ok_trough,
                    "peak_within_0.10pp": n_ok10p, "trough_within_0.10pp": n_ok10t,
                    "median_peak_diff_pp": round(st.median(dpeak), 4) if dpeak else None,
                    "median_trough_diff_pp": round(st.median(dtrough), 4) if dtrough else None,
                    "max_abs_peak_diff_pp": round(max((abs(x) for x in dpeak), default=0), 3),
                }
    return report


# ─────────────────────────── 阶段2：现规则复现 + 穿透 ───────────────────────────

MECHANICAL = ("sl", "tp", "breakeven_tp", "exit_policy:sl_pct", "staged_tp2_clear")


def stage_reproduce(trades: List[Dict], exchange: str, post_h: float, source: str = "kline") -> Dict:
    rows, pen = [], []
    loader = bars_loader(source)
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for t in trades:
            if str(t["close_reason"] or "") not in MECHANICAL:
                continue
            o = epoch_of(t["opened_at"], CST)
            c = epoch_of(t["closed_at"], CST)
            bars = loader(cur, t["symbol"], exchange, o, int(c + post_h * 3600))
            if len(bars) < 3:
                continue
            side, entry = t["side"], float(t["entry_price"])
            line = float(t["sl_price"] if t["close_reason"] != "tp" else t["tp_price"])
            hit = None
            for ts, _o, hi, lo, _c in bars:
                if str(side).lower().startswith("l"):
                    if lo <= line:
                        hit = ts
                        break
                else:
                    if hi >= line:
                        hit = ts
                        break
            if hit is None:
                rows.append({"id": t["id"], "reason": t["close_reason"], "delta_min": None})
                continue
            rows.append({"id": t["id"], "reason": t["close_reason"], "delta_min": round((hit - c) / 60, 1)})
            pen.append(dir_ret(side, entry, float(t["close_price"])) * 100 - dir_ret(side, entry, line) * 100)
    deltas = [abs(r["delta_min"]) for r in rows if r["delta_min"] is not None]
    return {
        "n_mechanical": len(rows), "n_line_touched": len(deltas),
        "median_abs_delta_min": round(st.median(deltas), 1) if deltas else None,
        "p90_abs_delta_min": round(sorted(deltas)[int(len(deltas) * 0.9)], 1) if deltas else None,
        "within_5min": sum(1 for d in deltas if d <= 5), "within_30min": sum(1 for d in deltas if d <= 30),
        "penetration_median_pp": round(st.median(pen), 3) if pen else 0.0,
        "penetration_p90_pp": round(sorted(pen)[int(len(pen) * 0.9)], 3) if len(pen) > 2 else None,
        "worst": sorted([r for r in rows if r["delta_min"] is None or abs(r["delta_min"]) > 30],
                        key=lambda r: -(abs(r["delta_min"]) if r["delta_min"] is not None else 999))[:8],
    }


# ─────────────────────────── 阶段3：反事实网格 ───────────────────────────

BASE = {"sl_cap": 1.5, "act": 2.5, "cb": 1.2, "be_buf": 1.5, "tp_pct": 6.75,
        "max_hold_h": None, "be_trigger": 0.8}


def replay_trade(t: Dict, bars, cfg: Dict, pen_pp: float, fee_pp: float) -> Dict:
    side = t["side"]
    long_ = str(side).lower().startswith("l")
    entry = float(t["entry_price"])
    sign = 1.0 if long_ else -1.0
    cap = cfg["sl_cap"] / 100.0
    sl = entry * (1 - sign * cap)
    tp = entry * (1 + sign * cfg["tp_pct"] / 100.0)
    peak = 0.0
    o_ep = epoch_of(t["opened_at"], CST)
    c_ep = epoch_of(t["closed_at"], CST)
    hold_h = cfg["max_hold_h"]
    exit_px, reason, exit_ts = None, None, None
    for ts, _o, hi, lo, cl in bars:
        # 1) 先用**上一根之后已知**的峰值棘轮（不看未来）
        if cfg["act"] is not None and peak >= cfg["act"]:
            cand = entry * (1 + sign * (peak - cfg["cb"]) / 100.0)
            sl = max(sl, cand) if long_ else min(sl, cand)
        if peak >= cfg["be_trigger"]:
            cand = entry * (1 + sign * cfg["be_buf"] / 100.0)
            sl = max(sl, cand) if long_ else min(sl, cand)
        # 2) 棒内：先看不利极值（保守），再看有利极值
        adv, fav = (lo, hi) if long_ else (hi, lo)
        if (long_ and adv <= sl) or ((not long_) and adv >= sl):
            exit_px = sl * (1 - sign * pen_pp / 100.0)
            reason = "trail" if ((sl - entry) * sign) > 0 else "sl"
            exit_ts = ts
            break
        if (long_ and fav >= tp) or ((not long_) and fav <= tp):
            exit_px, reason, exit_ts = tp, "tp", ts
            break
        # 3) 更新峰值（含收盘）
        ext = max(hi, cl) if long_ else min(lo, cl)
        peak = max(peak, dir_ret(side, entry, ext) * 100)
        # 4) 时间上限
        if hold_h and (ts - o_ep) >= hold_h * 3600:
            exit_px, reason, exit_ts = cl, "time", ts
            break
        # 5) 实际信号出场（不可重放 ⇒ 沿用实际时间与价格）
        if ts >= c_ep:
            exit_px, reason, exit_ts = float(t["close_price"]), str(t["close_reason"]), ts
            break
    if exit_px is None:
        exit_px = bars[-1][4] if bars else entry
        reason = "eod"
        exit_ts = bars[-1][0] if bars else c_ep
    gross = dir_ret(side, entry, exit_px) * 100
    net = gross - fee_pp
    notional = float(t["size"]) * entry
    return {"id": t["id"], "symbol": t["symbol"], "reason": reason, "gross_pp": gross,
            "net_pp": net, "pnl_usd": net / 100.0 * notional, "notional": notional,
            "hold_min": round((exit_ts - o_ep) / 60, 1), "peak_pp": peak}


def summarize(rs: List[Dict]) -> Dict:
    if not rs:
        return {}
    nets = [r["net_pp"] for r in rs]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    usd = sum(r["pnl_usd"] for r in rs)
    aw = st.mean(wins) if wins else 0.0
    al = st.mean(losses) if losses else 0.0
    return {
        "n": len(rs), "total_pp": round(sum(nets), 2), "exp_pp": round(st.mean(nets), 4),
        "total_usd": round(usd, 2), "exp_usd": round(usd / len(rs), 2),
        "win_rate": round(len(wins) / len(rs) * 100, 1),
        "avg_win_pp": round(aw, 3), "avg_loss_pp": round(al, 3),
        "rr": round(aw / abs(al), 2) if al else None,
        "worst_pp": round(min(nets), 3), "best_pp": round(max(nets), 3),
        "median_hold_min": round(st.median([r["hold_min"] for r in rs]), 1),
    }


def boot_ci(xs: List[float], n: int = 2000, seed: int = 7) -> Tuple[float, float]:
    import random
    rnd = random.Random(seed)
    if len(xs) < 5:
        return (0.0, 0.0)
    ms = []
    for _ in range(n):
        ms.append(st.mean([xs[rnd.randrange(len(xs))] for _ in xs]))
    ms.sort()
    return round(ms[int(n * 0.025)], 4), round(ms[int(n * 0.975)], 4)


def stage_grid(trades: List[Dict], exchange: str, pen_pp: float, fee_pp: float, post_h: float,
               source: str = "kline") -> Dict:
    bars_cache: Dict[int, List] = {}
    loader = bars_loader(source)
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for t in trades:
            o = epoch_of(t["opened_at"], CST)
            c = epoch_of(t["closed_at"], CST)
            bars_cache[t["id"]] = loader(cur, t["symbol"], exchange, o, int(c + post_h * 3600))
    usable = [t for t in trades if len(bars_cache.get(t["id"]) or []) >= 3]

    grid: List[Dict] = [dict(BASE, name="baseline(当前参数)")]
    for cap in (2.0, 2.5, 3.0, 4.5):
        grid.append(dict(BASE, sl_cap=cap, name=f"SL cap {cap}%"))
    for act, cb in ((3.0, 1.5), (3.5, 1.5), (3.5, 2.0), (4.0, 2.0), (None, None)):
        grid.append(dict(BASE, act=act, cb=cb or 1.2, name=f"追踪 {act}/{cb}"))
    for tp in (8.6, 10.0, 20.0):
        grid.append(dict(BASE, tp_pct=tp, name=f"TP {tp}%"))
    for buf in (0.8, 0.3):
        grid.append(dict(BASE, be_buf=buf, name=f"保本缓冲 {buf}%"))
    for h in (24.0, 48.0, 168.0):
        grid.append(dict(BASE, max_hold_h=h, name=f"持仓上限 {h:g}h"))
    # 组合与"P0 键名修复"的含义：声明的 SL/TP 生效（SL 1.5% 不变、TP 变 8.6~10%）
    grid.append(dict(BASE, tp_pct=9.5, name="组合:SL2.5+追踪3.5/2.0+TP9.5"))
    grid.append(dict(BASE, sl_cap=2.5, act=3.5, cb=2.0, tp_pct=9.5, max_hold_h=48.0,
                     name="组合2:SL2.5+3.5/2.0+TP9.5+48h"))

    results = {}
    base_sum = None
    for cfg in grid:
        rs = [replay_trade(t, bars_cache[t["id"]], cfg, pen_pp, fee_pp) for t in usable]
        s = summarize(rs)
        s["net_list"] = [round(r["net_pp"], 4) for r in rs]
        s["reason_mix"] = {k: sum(1 for r in rs if r["reason"] == k) for k in set(r["reason"] for r in rs)}
        s["ci95_exp_pp"] = boot_ci(s["net_list"])
        if cfg["name"].startswith("baseline"):
            base_sum = s
        results[cfg["name"]] = s
        print("  %-34s n=%d 期望=%+.4f%%/笔 总=%+.1f%% ($%+.0f) 胜率=%s%% 盈亏比=%s 最差=%s%%"
              % (cfg["name"], s["n"], s["exp_pp"], s["total_pp"], s["total_usd"], s["win_rate"], s["rr"], s["worst_pp"]))

    # 稳健性：对半切 + 留一币（只对"期望 > 基线"的配置做，省算力）
    robust = {}
    half = len(usable) // 2
    for cfg in grid:
        name = cfg["name"]
        s = results[name]
        if base_sum and s["exp_pp"] <= base_sum["exp_pp"] and not name.startswith("baseline"):
            continue
        fh = summarize([replay_trade(t, bars_cache[t["id"]], cfg, pen_pp, fee_pp) for t in usable[:half]])
        sh = summarize([replay_trade(t, bars_cache[t["id"]], cfg, pen_pp, fee_pp) for t in usable[half:]])
        per_sym = {}
        syms = sorted(set(t["symbol"] for t in usable))
        worst_drop = None
        for sym in syms:
            sub = [t for t in usable if t["symbol"] != sym]
            if len(sub) < 100:
                continue
            ss = summarize([replay_trade(t, bars_cache[t["id"]], cfg, pen_pp, fee_pp) for t in sub])
            per_sym[sym] = ss["exp_pp"]
            if worst_drop is None or ss["exp_pp"] < worst_drop:
                worst_drop = ss["exp_pp"]
        robust[name] = {"exp_first_half": fh.get("exp_pp"), "exp_second_half": sh.get("exp_pp"),
                        "leave_one_symbol_out_min_exp": worst_drop, "per_symbol": per_sym}
    return {"usable_trades": len(usable), "excluded": len(trades) - len(usable),
            "baseline": base_sum, "configs": {k: {kk: vv for kk, vv in v.items() if kk != "net_list"}
                                              for k, v in results.items()},
            "robustness": robust}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="中线出场结构反事实重放（只读）")
    ap.add_argument("--stage", default="all", choices=("validate", "reproduce", "grid", "all"))
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--account", type=int, default=14)
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--source", default="kline", choices=("kline", "agg"),
                    help="kline=1m K线；agg=15s 成交聚合桶（更细）")
    ap.add_argument("--post-hours", type=float, default=2.0)
    ap.add_argument("--validate-limit", type=int, default=25)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)

    trades = load_trades(a.days, a.account)
    print("样本: %d 笔 mid 已平仓（近 %d 天）" % (len(trades), a.days))
    out: Dict = {"days": a.days, "n_trades": len(trades), "generated_at": dt.datetime.now().isoformat(timespec="seconds")}

    if a.stage in ("validate", "all"):
        print("\n[阶段1] K 线对齐验收（MFE/MAE 对比，按价源×时区）")
        v = stage_validate(trades, a.days, a.post_hours, a.validate_limit)
        out["validate"] = v
        best = None
        for k, r in sorted(v.items(), key=lambda kv: -(kv[1]["peak_within_0.10pp"] + kv[1]["trough_within_0.10pp"])):
            print("   %-24s n=%-3d 命中±0.10pp(peak/trough)=%d/%d  ±0.15pp=%d/%d  中位差 peak=%s trough=%s"
                  % (k, r["n_used"], r["peak_within_0.10pp"], r["trough_within_0.10pp"],
                     r["peak_within_0.15pp"], r["trough_within_0.15pp"],
                     r["median_peak_diff_pp"], r["median_trough_diff_pp"]))
            if best is None and k.endswith("|CST"):
                best = (k, r)
        print("   ⇒ 最优价源/时区: %s" % (best[0] if best else "-"))
        if best and (best[1]["peak_within_0.15pp"] + best[1]["trough_within_0.15pp"]) < best[1]["n_used"]:
            print("   ⚠ 未过验收阈值（±0.15pp 两项命中数应 ≈ n）：**不得**据此下结论，先查时区/价源")
        if a.exchange == "auto" and best:
            src, exch = best[0].split("|")[0].split(":")
            a.source, a.exchange = src, exch
            print("   ⇒ 自动选用价源: %s:%s" % (a.source, a.exchange))

    out["exchange"] = a.exchange
    out["source"] = a.source
    if a.stage in ("reproduce", "all"):
        print("\n[阶段2] 现规则复现 + 穿透校准（%s:%s）" % (a.source, a.exchange))
        rp = stage_reproduce(trades, a.exchange, a.post_hours, a.source)
        out["reproduce"] = rp
        for k in ("n_mechanical", "n_line_touched", "median_abs_delta_min", "p90_abs_delta_min",
                  "within_5min", "within_30min", "penetration_median_pp", "penetration_p90_pp"):
            print("   %-24s %s" % (k, rp.get(k)))
        print("   未对齐样本: %s" % rp.get("worst"))

    if a.stage in ("grid", "all"):
        rp = out.get("reproduce") or {}
        pen = float(rp.get("penetration_median_pp") or 0.0)
        fee = 0.10
        print("\n[阶段3] 反事实网格（%s:%s；穿透按阶段2 校准 = %.3fpp，手续费按 %.2fpp/笔计）"
              % (a.source, a.exchange, pen, fee))
        out["grid"] = stage_grid(trades, a.exchange, pen, fee, a.post_hours, a.source)

    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print("\n写入 %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
