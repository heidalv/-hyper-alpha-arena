# -*- coding: utf-8 -*-
"""[2026-09-23] 止盈止损专项：5 个长线仓（4 open + LINK 已平）的利润回吐反事实重放。

用户反馈："利润回吐了 100u，基本没有有效的执行，主要研究止盈止损这里。"

这 5 仓的共同点（实测）：TP=None、trail=None；只有 SL（XRP/BTC 在成本上方 0.5~1.65%，
ETH/SOL/LINK 在成本下方 3%）。盈利无任何锁定机制 ⇒ 峰值→现在/平仓回吐 ≈ $100。

本脚本对每仓从入场到现在的 1m 路径重放候选止盈/止损规则，回答：
"如果当时有规则 X，现在手里是多少钱"。全部只读。

规则候选（美元口径，含同一双边手续费 0.10pp 占位，比较的是差额）：
  A 现状（实际）          —— 对照
  B 追踪 3.0/1.5           —— 影子 V2=D2b
  C 追踪 5.0/2.5
  D 分批 50%@5% + 25%@8% + 余仓追踪 3.0/1.5   —— 影子 V1=D1b
  E 全平 TP 10%
  F 全平 TP 8%
  G 车道声明档 8/15/25（50/25/25）           —— 目前只声明不执行
  H 现状 + 保本后追踪 3.0/1.5（成本上方后启用） —— 保本组合
"""
from __future__ import annotations

import io
import statistics as st
import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena\scripts")
import cf_mid_trail_grid as G  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PEN_PP = G.PEN_PP
FEE_PP = G.FEE_PP

# 5 个目标仓：position_id -> (symbol, side, entry, size, sl_price, opened_at, closed_at or None, close_price)
import psycopg

ARENA = G.ARENA


def load_targets():
    import json as _json
    with psycopg.connect(ARENA, autocommit=True) as c:
        cur = c.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """SELECT id, symbol, side, entry_price, size, sl_price, tp_price, opened_at, closed_at,
                      close_price, status, exit_state_json, unrealized_pnl, peak_unrealized_pnl
               FROM paper_positions
               WHERE account_id=14 AND id IN (4712,4730,4743,4751,4752)""")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for t in rows:
        t["now_pnl"] = float(t.get("unrealized_pnl") or 0)
        t["entry_sl_pct"] = None
        try:
            d = _json.loads(t["exit_state_json"] or "{}")
            pol = d.get("exit_policy") or {}
            if pol.get("sl_pct"):
                t["entry_sl_pct"] = float(pol["sl_pct"])
            if d.get("structural_stop_price"):
                t["structural_stop"] = float(d["structural_stop_price"])
        except Exception:
            pass
        # [2026-09-23 修正2] 真实执行的硬止损：4 个 open 仓实测跌破声明 sl_pct 未被扫
        # （BTC 低至 −3.6%、XRP 低至 −11%）⇒ 用 structural_stop_price（Chandelier）做底线；
        # LINK 已按 −3.2% SL 实际平仓 ⇒ 用其快照 SL 复现。
        if t.get("close_price"):
            t["entry_sl"] = (float(t["entry_price"]) * (1 - t["entry_sl_pct"] / 100.0)
                             if t["entry_sl_pct"] else float(t["sl_price"]))
        else:
            t["entry_sl"] = t.get("structural_stop") or (
                float(t["entry_price"]) * (1 - t["entry_sl_pct"] / 100.0)
                if t["entry_sl_pct"] else None)
    return rows


def load_path(cur, symbol, t0, t1):
    cur.execute(
        """select timestamp, open_price, high_price, low_price, close_price from crypto_klines
           where symbol=%s and exchange='binance' and period='1m' and environment='mainnet'
             and timestamp between %s and %s order by timestamp""",
        (symbol, t0, t1))
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in cur.fetchall()]


def replay(t, bars, rule):
    """返回 dict: outcome=closed|open, pnl_usd, why, exit_px, locked_levels"""
    side = t["side"]
    long_ = str(side).lower().startswith("l")
    sign = 1.0 if long_ else -1.0
    entry = float(t["entry_price"])
    size = float(t["size"])
    notional = entry * size
    # [2026-09-23 修正] 用**入场时**的 SL（exit_state_json.exit_policy.sl_pct，2.5~3.2%）：
    # DB 的 sl_price 是**当前**值（BTC/XRP 已被系统抬到成本上方），拿它回放整条路径
    # 会在历史低点提前触发（实测回放与真实持仓矛盾）。候选规则用入场 SL 起手。
    sl = t.get("entry_sl")
    peak = 0.0
    trail_active = False
    trail_sl = None
    rem = 1.0
    realized = 0.0
    stages = rule.get("stages") or []   # [(pct, ratio), ...]
    fired = []
    trail = rule.get("trail")           # (act, cb)
    tp_full = rule.get("tp_full")
    breakeven_arm = rule.get("breakeven_arm")  # 保本后再追踪

    for ts, _op, hi, lo, cl in bars:
        ext = max(hi, cl) if long_ else min(lo, cl)
        pct = ((ext - entry) / entry) * sign * 100
        peak = max(peak, pct)
        # 分批止盈（对剩余仓位的比例按声明比率平掉）
        for (sp, ratio) in list(stages):
            if peak >= sp and sp not in fired:
                fired.append(sp)
                realized += rem * ratio * (sp / 100.0) * notional
                rem -= rem * ratio
                if trail:
                    trail_active = True
                if rem <= 1e-9:
                    return {"outcome": "closed", "pnl": realized - FEE_PP / 100 * notional,
                            "why": "staged_all", "exit_px": ext, "fired": fired}
        # 追踪线（在启用后按 peak-cb 更新）
        if trail:
            act, cb = trail
            if (not breakeven_arm) or (breakeven_arm and peak > 0):
                if peak >= act or trail_active:
                    trail_active = True
                    line = entry * (1 + sign * (peak - cb) / 100.0)
                    if trail_sl is None or (line > trail_sl if long_ else line < trail_sl):
                        trail_sl = line
        # 保本：现有 SL 与追踪线取更优
        eff_sl = sl
        if trail_sl is not None:
            eff_sl = max(sl, trail_sl) if (long_ and sl is not None) else trail_sl
            if long_ and sl is None:
                eff_sl = trail_sl
        # 触发检查（保守：棒内先看不利用方向极值）
        adv, fav = (lo, hi) if long_ else (hi, lo)
        if eff_sl is not None and ((adv <= eff_sl) if long_ else (adv >= eff_sl)):
            exit_px = eff_sl * (1 - sign * PEN_PP / 100.0)
            pnl = (exit_px - entry) / entry * sign * 100
            return {"outcome": "closed", "pnl": realized + rem * pnl / 100 * notional
                    - FEE_PP / 100 * notional, "why": "trail_sl" if trail_sl else "sl",
                    "exit_px": exit_px, "fired": fired}
        if tp_full and ((fav >= entry * (1 + sign * tp_full / 100.0)) if long_
                        else (fav <= entry * (1 + sign * tp_full / 100.0))):
            return {"outcome": "closed", "pnl": realized + rem * tp_full / 100 * notional
                    - FEE_PP / 100 * notional, "why": "tp_full", "exit_px": ext, "fired": fired}
    # 走完全程未触发：open 仓按最后一根收盘计浮动；已平仓按实际收盘价
    final_px = float(t["close_price"]) if t.get("close_price") else bars[-1][4]
    pnl = (final_px - entry) / entry * sign * 100
    return {"outcome": "open" if not t.get("close_price") else "actual_close",
            "pnl": realized + rem * pnl / 100 * notional - FEE_PP / 100 * notional,
            "why": "hold", "exit_px": final_px, "fired": fired}


def main() -> int:
    targets = load_targets()
    rules = [
        ("A 现状(实际)", {}),
        ("B 追踪3.0/1.5", {"trail": (3.0, 1.5)}),
        ("C 追踪5.0/2.5", {"trail": (5.0, 2.5)}),
        ("D 分批50%@5+25%@8+追踪3/1.5", {"stages": [(5.0, 0.5), (8.0, 0.5)], "trail": (3.0, 1.5)}),
        ("E 全平TP10%", {"tp_full": 10.0}),
        ("F 全平TP8%", {"tp_full": 8.0}),
        ("G 声明档8/15/25(50/25/25,无追踪)", {"stages": [(8.0, 0.5), (15.0, 0.5), (25.0, 0.5)]}),
        ("H 保本后追踪3/1.5", {"trail": (3.0, 1.5), "breakeven_arm": True}),
    ]
    paths = {}
    with psycopg.connect(G.MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        now = int(__import__("time").time())
        for t in targets:
            o = int(t["opened_at"].replace(tzinfo=G.CST).timestamp())
            paths[t["id"]] = load_path(cur, t["symbol"], o, now)
    print("仓位: " + "  ".join("%s=%s" % (t["symbol"], t["id"]) for t in targets))
    print()
    hdr = "  %-26s" % "规则"
    for t in targets:
        hdr += " %12s" % t["symbol"]
    hdr += " %10s" % "合计"
    print(hdr)
    rows_out = {}
    for name, rule in rules:
        line = "  %-26s" % name
        tot = 0.0
        vals = []
        for t in targets:
            if name == "A 现状(实际)":
                # 用 DB 事实：open=当前浮盈（mark），LINK=实际 sl 平仓
                if t.get("close_price"):
                    sign = 1.0 if str(t["side"]).lower().startswith("l") else -1.0
                    pnl = (float(t["close_price"]) - float(t["entry_price"])) / float(t["entry_price"]) \
                        * sign * 100 / 100.0 * (float(t["entry_price"]) * float(t["size"])) \
                        - FEE_PP / 100 * (float(t["entry_price"]) * float(t["size"]))
                else:
                    pnl = t["now_pnl"]
                r = {"outcome": "actual", "pnl": pnl, "why": "actual", "exit_px": None, "fired": []}
            else:
                r = replay(t, paths[t["id"]], rule)
            vals.append(r)
            tot += r["pnl"]
            line += " %+12.2f" % r["pnl"]
        line += " %+10.2f" % tot
        rows_out[name] = (tot, vals)
        print(line)
    print()
    print("细节（每规则每仓的出场方式/触发档；A 行为 DB 事实）：")
    for name, rule in rules:
        for t in targets:
            if name == "A 现状(实际)":
                continue
            r = replay(t, paths[t["id"]], rule)
            note = "%s(%s)" % (r["why"], ",".join(str(f) for f in r.get("fired") or []))
            print("  %-26s %-7s %-28s pnl=%+.2f" % (name, t["symbol"], note, r["pnl"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
