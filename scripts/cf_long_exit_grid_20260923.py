"""[2026-09-23] 长线(tier=long) 出场方向 D1-D7 **参数回测**（只读，只写报告）。

方法与中线同一套最小模型：逐笔真实路径（kline 1m binance 或 agg 15s）+ 逐笔自身参数 +
信号类出场按实际时刻/价格封顶（不可重放）；机械规则重裁。保守口径：棒内先看不利方向。
- 基线 = 该笔自身 `sl_price`（Chandelier 结构止损，实测距 entry 中位 10.24%、每日只上移）为唯一机械出口，
  TP/追踪/时间上限全关（与长线现状一致：exit_policy long 默认全 None、tp_stages 声明不执行）。
- 变体 = D1 分档/部分止盈、D2 追踪组合、D3 金字塔补仓、D4 部分止盈+时间上限、D6 时间上限。
- D5（挂单）与 D7（监控）是成本/监控项，结果见 §1.4 与报告正文，此处不测。
- 金字塔：浮盈门槛触发后在当根**收盘价**加仓（保守），比例按配置；"移损"= 加仓后全仓止损上移到
  最新加仓价 − 初始风险距离（海龟式），"不移损"= 保持原止损。
- 费用：每腿 taker 4bp（入场+出场，asterdex 口径）；资金费实测 $0.02/笔忽略（注明）。
- 部分止盈：按阈值在当根不利先判后、以阈值价成交，每档只一次；已实现收益累计入账。

验收（写死）：双价源 Δ 方向一致；前后半 Δ 都 ≥ 0；最差单笔不显著恶化；E1 子样本（25 笔）单独列。
用法：.venv\\Scripts\\python.exe scripts/cf_long_exit_grid_20260923.py --source kline
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import statistics as st
import sys
from typing import Dict, List

import psycopg

ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
FEE_BP = 4.0
MECH_LONG = {"sl", "breakeven_tp", "emergency_drawdown", "profit_drawdown_stage",
             "profit_drawdown_full", "profit_drawdown_hard", "trend_review_close"}


def is_mech(reason: str) -> bool:
    r = str(reason or "")
    return r in MECH_LONG or r.startswith("long_trend_v2:")


def load_trades(days: str) -> List[Dict]:
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, side, entry_price, size, leverage, opened_at, closed_at,
                      close_price, close_reason, sl_price, tp_price, exit_state_json,
                      coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0) pnl_usd
               from paper_positions
               where account_id=14 and status='closed' and timeframe_tier='long'
                 and closed_at > now() - (%s || ' days')::interval
               order by opened_at""", (days,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for t in rows:
        src = None
        try:
            d = json.loads(t["exit_state_json"] or "{}")
            src = d.get("entry_source") or (d.get("open_metadata") or {}).get("entry_source")
            if d.get("structural_stop_price"):
                t["structural_stop"] = float(d["structural_stop_price"])
        except Exception:
            pass
        t["is_e1"] = str(src or "").strip().lower() == "trend_e1"
    return rows


def load_path(cur, symbol: str, exchange: str, t0: int, t1: int, source: str):
    if source == "agg":
        cur.execute(
            """select timestamp, high_price, low_price, vwap from market_trades_aggregated
               where symbol=%s and exchange=%s and timestamp between %s and %s order by timestamp""",
            (symbol, exchange, t0 * 1000, t1 * 1000))
        return [(int(r[0]) // 1000, float(r[3] or r[1]), float(r[1]), float(r[2]), float(r[3] or r[1]))
                for r in cur.fetchall()]
    cur.execute(
        """select timestamp, open_price, high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange=%s and period='1m' and environment='mainnet'
             and timestamp between %s and %s order by timestamp""",
        (symbol, exchange, t0, t1))
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in cur.fetchall()]


def dirret(side: str, entry: float, px: float) -> float:
    r = (px - entry) / entry
    return r if str(side).lower().startswith("l") else -r


def simulate(t: Dict, bars, cfg: Dict, fee_bp: float = FEE_BP) -> Dict:
    long_ = str(t["side"]).lower().startswith("l")
    sign = 1.0 if long_ else -1.0
    entry = float(t["entry_price"])
    base_notional = float(t["size"]) * entry
    sl0 = float(t["sl_price"] or 0)
    # [2026-09-23 修正] sl_price 是活体字段（平仓时=棘轮后锁利线），不能当初始止损——
    # 否则回放会在 t=0 就用"最终锁利线"砍仓（基线总盈亏因此偏差 −$628）。
    # [2026-09-23 修正2] 改为逐笔读 exit_state_json.structural_stop_price（Chandelier 结构止损，
    # 实测才是真实执行的硬底线：BTC −5.8%、XRP −19.6%、LINK −16.9%）；缺失时回退
    # 实测初始结构止损距离中位 10.24%（E1 30 笔实测）。逐笔 > 全局近似。
    if t.get("structural_stop") and t["structural_stop"] > 0:
        sl0 = t["structural_stop"]
    else:
        sl0 = entry * (1 - sign * 0.1024)
    risk_dist = abs(entry - sl0) / entry * 100
    keep_signal = not is_mech(t["close_reason"])
    c_ep = int(t["closed_at"].replace(tzinfo=CST).timestamp())
    o_ep = int(t["opened_at"].replace(tzinfo=CST).timestamp())

    legs: List[List[float]] = [[1.0, entry, sl0]]     # [份额, 入场价, 止损价]
    realized = 0.0                                      # 部分止盈已实现净额
    fees_acc = fee_bp / 10000.0 * base_notional         # 初始入场费
    act = cfg.get("act")
    cb = cfg.get("cb") or 0.0
    peak = 0.0
    tp = cfg.get("tp_pct")
    partial = cfg.get("partial") or []
    partial_done = set()
    pyr = cfg.get("pyramid")
    pyr_idx = 0
    tl_h = cfg.get("time_limit_h")
    exit_ts, why, final_pnl = None, None, None

    def realize_at(px: float, share: float) -> None:
        nonlocal realized, fees_acc
        cut_remaining = share
        new_legs: List[List[float]] = []
        for leg in legs:
            if cut_remaining > 1e-9 and leg[0] > 1e-9:
                cut = min(leg[0], cut_remaining)
                realized += dirret(t["side"], leg[1], px) * base_notional * cut
                fees_acc += fee_bp / 10000.0 * base_notional * cut
                leg[0] -= cut
                cut_remaining -= cut
            if leg[0] > 1e-9:
                new_legs.append(leg)
        legs[:] = new_legs

    for ts, _op, hi, lo, cl in bars:
        if tl_h and (ts - o_ep) >= tl_h * 3600:
            exit_ts, why = ts, "time_limit"
            final_pnl = 0.0
            for leg in legs:
                final_pnl += dirret(t["side"], leg[1], cl) * base_notional * leg[0]
                fees_acc += fee_bp / 10000.0 * base_notional * leg[0]
            break
        if act is not None and peak >= act:
            cand = entry * (1 + sign * (peak - cb) / 100.0)
            for leg in legs:
                leg[2] = max(leg[2], cand) if long_ else min(leg[2], cand)
        adv, fav = (lo, hi) if long_ else (hi, lo)
        # [2026-09-23 深夜加测] 亏损硬闸：任何时刻亏过 cap% 即全平（比结构止损更严的绝对上限，
        # 只在大亏尾巴上起作用——thesis_invalidation/sl_pct 类"让子弹飞"出场是长线 −$88.89 的来源）
        if cfg.get("loss_cap"):
            cap_px = entry * (1 - sign * cfg["loss_cap"] / 100.0)
            hit_cap = (adv <= cap_px) if long_ else (adv >= cap_px)
            if hit_cap:
                exit_ts, why = ts, "loss_cap"
                final_pnl = 0.0
                for leg in legs:
                    final_pnl += dirret(t["side"], leg[1], cap_px) * base_notional * leg[0]
                    fees_acc += fee_bp / 10000.0 * base_notional * leg[0]
                break
        # [2026-09-23 修正] 方向判定曾写反（l[2]<=adv ⇒ 止损在现价下方恒真，首棒即全平）。
        # 正确：多头价格下穿止损 ⇒ adv(lo) <= sl ⇒ sl >= adv；触发线=多腿中**先被击穿**（最高）的那根。
        hit_sl = (any(l[2] >= adv for l in legs)) if long_ else (any(l[2] <= adv for l in legs))
        if hit_sl:
            exit_ts, why = ts, "sl"
            px = max(l[2] for l in legs) if long_ else min(l[2] for l in legs)
            final_pnl = 0.0
            for leg in legs:
                final_pnl += dirret(t["side"], leg[1], px) * base_notional * leg[0]
                fees_acc += fee_bp / 10000.0 * base_notional * leg[0]
            break
        if tp:
            thr_px = entry * (1 + sign * tp / 100.0)
            if ((fav >= thr_px) if long_ else (fav <= thr_px)):
                exit_ts, why = ts, "tp"
                final_pnl = 0.0
                for leg in legs:
                    final_pnl += dirret(t["side"], leg[1], thr_px) * base_notional * leg[0]
                    fees_acc += fee_bp / 10000.0 * base_notional * leg[0]
                break
        for i, (thr, share) in enumerate(partial):
            thr_px = entry * (1 + sign * thr / 100.0)
            hit = (fav >= thr_px) if long_ else (fav <= thr_px)
            if hit and i not in partial_done:
                realize_at(thr_px, share)
                partial_done.add(i)
        if pyr:
            thrs, ratios, move_sl = pyr
            while pyr_idx < len(thrs):
                thr_px = entry * (1 + sign * thrs[pyr_idx] / 100.0)
                hit = (cl >= thr_px) if long_ else (cl <= thr_px)
                if not hit:
                    break
                add_px = cl
                add_sl = add_px * (1 - sign * risk_dist / 100.0)
                legs.append([ratios[pyr_idx], add_px, add_sl])
                fees_acc += fee_bp / 10000.0 * base_notional * ratios[pyr_idx]
                if move_sl:
                    for leg in legs:
                        leg[2] = add_sl
                pyr_idx += 1
        ext = max(hi, cl) if long_ else min(lo, cl)
        peak = max(peak, dirret(t["side"], entry, ext) * 100)
        if keep_signal and ts >= c_ep:
            exit_ts, why = ts, "signal_actual"
            final_pnl = 0.0
            for leg in legs:
                final_pnl += dirret(t["side"], leg[1], float(t["close_price"])) * base_notional * leg[0]
                fees_acc += fee_bp / 10000.0 * base_notional * leg[0]
            break
    if exit_ts is None:
        exit_ts, why = bars[-1][0], "data_end"
        final_pnl = 0.0
        for leg in legs:
            final_pnl += dirret(t["side"], leg[1], bars[-1][4]) * base_notional * leg[0]
            fees_acc += fee_bp / 10000.0 * base_notional * leg[0]
    return {"id": t["id"], "why": why, "pnl_usd": (final_pnl or 0.0) + realized - fees_acc,
            "actual_usd": float(t["pnl_usd"] or 0), "opened_at": t["opened_at"]}


def stats(rows: List[Dict]) -> Dict:
    if not rows:
        return {"n": 0, "delta_usd": 0.0, "delta_mean": 0.0, "total_usd": 0.0, "win_rate": 0.0, "worst": 0.0}
    d = [r["pnl_usd"] - r["actual_usd"] for r in rows]
    usd = [r["pnl_usd"] for r in rows]
    wins = [x for x in usd if x > 0]
    return {"n": len(rows), "delta_usd": round(sum(d), 2), "delta_mean": round(st.mean(d), 3),
            "total_usd": round(sum(usd), 2), "win_rate": round(100 * len(wins) / len(usd), 1),
            "worst": round(min(usd), 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="90")
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--source", default="kline", choices=("agg", "kline"))
    ap.add_argument("--post-hours", type=float, default=72.0)
    a = ap.parse_args(argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    trades = load_trades(a.days)
    e1n = sum(1 for t in trades if t["is_e1"])
    print("样本: long 90天 %d 笔（E1 %d / 非E1 %d）；源=%s:%s"
          % (len(trades), e1n, len(trades) - e1n, a.source, a.exchange))
    paths: Dict[int, List] = {}
    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for t in trades:
            o = int(t["opened_at"].replace(tzinfo=CST).timestamp())
            c = int(t["closed_at"].replace(tzinfo=CST).timestamp())
            paths[t["id"]] = load_path(cur, t["symbol"], a.exchange, o,
                                       max(c, o + 3600) + int(a.post_hours * 3600), a.source)
    usable = [t for t in trades if len(paths.get(t["id"]) or []) >= 5]
    half = len(usable) // 2
    by_id = {t["id"]: t for t in usable}
    cfgs = [
        {"name": "基线(仅结构止损,现状)", "act": None, "tp_pct": None, "partial": [],
         "pyramid": None, "time_limit_h": None},
        {"name": "D1a 部分50%@3%+追踪3/1.5", "act": 3.0, "cb": 1.5, "partial": [(3.0, 0.5)]},
        {"name": "D1b 分档50%@5%+25%@8%+追踪3/1.5", "act": 3.0, "cb": 1.5, "partial": [(5.0, 0.5), (8.0, 0.25)]},
        {"name": "D1c 固定TP5%全平", "act": None, "tp_pct": 5.0, "partial": []},
        {"name": "D2a 全仓追踪5/2.5", "act": 5.0, "cb": 2.5},
        {"name": "D2b 全仓追踪3/1.5", "act": 3.0, "cb": 1.5},
        {"name": "D4 Li式75%@5%+追踪5/2.5+48h", "act": 5.0, "cb": 2.5, "partial": [(5.0, 0.75)],
         "time_limit_h": 48.0},
        {"name": "D6a 时间上限48h", "time_limit_h": 48.0},
        {"name": "D6b 时间上限72h", "time_limit_h": 72.0},
        {"name": "D3a 金字塔[1.5,3,5]%[0.5,0.3,0.2]移损", "pyramid": ([1.5, 3.0, 5.0], [0.5, 0.3, 0.2], True)},
        {"name": "D3b 金字塔+2.5%/两次0.5移损", "pyramid": ([2.5, 5.0], [0.5, 0.5], True)},
        {"name": "D3c 金字塔+2.5%两次0.5不移损", "pyramid": ([2.5, 5.0], [0.5, 0.5], False)},
        # [2026-09-23 深夜加测] 车道声明档（8/15/25 执行化）+ 固定全平 TP 档
        {"name": "声明档8/15/25(50/25/25)+余仓追5/2.5", "act": 5.0, "cb": 2.5,
         "partial": [(8.0, 0.5), (15.0, 0.25), (25.0, 0.25)]},
        {"name": "全平TP10%", "act": None, "tp_pct": 10.0, "partial": []},
        {"name": "全平TP15%", "act": None, "tp_pct": 15.0, "partial": []},
        # [2026-09-23 深夜加测] 亏损硬闸（大亏尾巴加闸，只比结构止损更严处生效）
        {"name": "-8%硬闸(现状+绝对上限)", "act": None, "loss_cap": 8.0},
        {"name": "-10%硬闸(现状+绝对上限)", "act": None, "loss_cap": 10.0},
        {"name": "追踪5/2.5 + -8%硬闸", "act": 5.0, "cb": 2.5, "loss_cap": 8.0},
        # [2026-09-24] 组合：已上线的声明档分档止盈 + 硬闸（这是"真正会合起来上线"的形态）
        {"name": "声明档8/15/25+余仓追5/2.5+-8%硬闸", "act": 5.0, "cb": 2.5,
         "partial": [(8.0, 0.5), (15.0, 0.25), (25.0, 0.25)], "loss_cap": 8.0},
    ]
    # 基线 = 实际成交（零偏差，保真度=1）；变体在 10.24% 初始止损上重裁
    base_rows = [{"id": t["id"], "why": "actual", "pnl_usd": float(t["pnl_usd"] or 0),
                  "actual_usd": float(t["pnl_usd"] or 0), "opened_at": t["opened_at"]} for t in usable]
    base_s = stats(base_rows)
    base_h1 = stats(base_rows[:half])
    base_h2 = stats(base_rows[half:])
    base_e1 = stats([r for r in base_rows if by_id[r["id"]]["is_e1"]])
    print("  基线(=实际成交): n=%d 总USD=%+.2f 胜率=%s%% 最差=%s"
          % (base_s["n"], base_s["total_usd"], base_s["win_rate"], base_s["worst"]))
    print("  基线保真度: 按实际成交定义（Δ=0 by construction）；变体用 10.24% 初始止损重裁，"
          "信号类出场仍按实际时刻/价格封顶")
    print("  %-34s %-10s %-10s %-9s %-9s %-9s %-9s" % ("配置", "Δ总USD", "Δ均/笔", "前半Δ", "后半Δ", "E1组Δ总", "最差单笔"))
    for cfg in cfgs[1:]:
        rows = [simulate(t, paths[t["id"]], cfg) for t in usable]
        s = stats(rows)
        fh = stats(rows[:half]); sh = stats(rows[half:])
        e1d = stats([r for r in rows if by_id[r["id"]]["is_e1"]])
        print("  %-34s %+-10.2f %+-10.3f %+-9.2f %+-9.2f %+-9.2f %+-9.2f"
              % (cfg["name"], s["delta_usd"], s["delta_mean"],
                 fh["delta_usd"] - base_h1["delta_usd"], sh["delta_usd"] - base_h2["delta_usd"],
                 e1d["delta_usd"] - base_e1["delta_usd"], s["worst"]))
    print("  基线最差单笔=%+.2f（尾部验收基准：采纳配置的最差单笔不得显著差于它）" % base_s["worst"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
