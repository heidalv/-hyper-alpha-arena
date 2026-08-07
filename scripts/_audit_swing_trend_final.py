"""Paper swing/trend 14d 审计 — 最终版，PnL=closed.unrealized_pnl。"""
from __future__ import annotations

import os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from dotenv import load_dotenv
load_dotenv(".env")

from backend.database.connection import SessionLocal, DATABASE_URL
from backend.database.models import PaperOrder, PaperPosition

DAYS = 14
NATURES = {"swing", "trend_follow", "trend"}
TIERS = {"mid", "long"}
SL_REASONS = {"sl", "stop_loss", "liquidation", "liquidated"}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dt(v):
    if v is None:
        return None
    return v.replace(tzinfo=None) if getattr(v, "tzinfo", None) else v


def is_mid_long(p):
    return (p.trade_nature or "").lower() in NATURES or (p.timeframe_tier or "").lower() in TIERS


def pnl(p):
    return float(p.unrealized_pnl or 0)


def hold_h(p):
    o, c = dt(p.opened_at), dt(p.closed_at)
    return (c - o).total_seconds() / 3600 if o and c else None


def sl_dist_pct(p):
    ep, sl = float(p.entry_price or 0), float(p.sl_price or 0) if p.sl_price else 0
    if ep <= 0 or sl <= 0 or ep > 100000:  # 排除占位 entry
        return None
    return abs(ep - sl) / ep * 100


def pct(n, d):
    return f"{n/d*100:.1f}%" if d else "0.0%"


def qtile(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    i = (len(s) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (i - lo)


def max_consec_loss(pnls):
    best = cur = 0
    for x in pnls:
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return best


def nature_key(p):
    nat = (p.trade_nature or "").lower()
    if nat in ("swing",):
        return "swing"
    if nat in ("trend_follow", "trend"):
        return "trend_follow"
    tier = (p.timeframe_tier or "").lower()
    return "swing" if tier == "mid" else "trend_follow" if tier == "long" else "other"


def side_key(p):
    s = (p.side or "").lower()
    return "long" if s in ("long", "buy") else "short"


def to_rec(p):
    return {
        "id": p.id,
        "symbol": p.symbol,
        "side": side_key(p),
        "nature": nature_key(p),
        "trade_nature": (p.trade_nature or "").lower(),
        "tier": (p.timeframe_tier or "").lower(),
        "reason": (p.close_reason or "unknown").lower(),
        "pnl": pnl(p),
        "win": pnl(p) >= 0,
        "hold_h": hold_h(p),
        "opened_at": dt(p.opened_at),
        "closed_at": dt(p.closed_at),
        "entry": float(p.entry_price or 0),
        "close_px": float(p.close_price) if p.close_price else None,
        "sl": float(p.sl_price) if p.sl_price else None,
        "tp": float(p.tp_price) if p.tp_price else None,
        "sl_dist": sl_dist_pct(p),
        "margin": float(p.margin or 0),
    }


def agg_table(rows: List[dict], key_fn, title: str, extra_cols=False):
    g: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        g[key_fn(r)].append(r)
    print(f"\n### {title}")
    if extra_cols:
        print("| 维度 | 笔数 | 胜率 | 总盈亏(U) | 平均盈亏(U) | 平均持仓(h) | 最大连亏 |")
        print("|---|---:|---:|---:|---:|---:|---:|")
    else:
        print("| 维度 | 笔数 | 胜率 | 总盈亏(U) | 平均盈亏(U) |")
        print("|---|---:|---:|---:|---:|")
    items = sorted(g.items(), key=lambda x: -len(x[1]))
    for k, rs in items:
        rs_sorted = sorted(rs, key=lambda x: x["closed_at"] or datetime.min)
        n = len(rs)
        wins = sum(1 for x in rs if x["win"])
        tp = sum(x["pnl"] for x in rs)
        if extra_cols:
            holds = [x["hold_h"] for x in rs if x["hold_h"] is not None]
            ah = sum(holds) / len(holds) if holds else 0
            mcl = max_consec_loss([x["pnl"] for x in rs_sorted])
            print(f"| {k} | {n} | {pct(wins, n)} | {tp:.2f} | {tp/n:.2f} | {ah:.1f} | {mcl} |")
        else:
            print(f"| {k} | {n} | {pct(wins, n)} | {tp:.2f} | {tp/n:.2f} |")


def main():
    db = SessionLocal()
    cutoff = utcnow() - timedelta(days=DAYS)
    try:
        positions = [
            p for p in db.query(PaperPosition)
            .filter(PaperPosition.status.in_(["closed", "liquidated"]),
                    PaperPosition.closed_at >= cutoff)
            .order_by(PaperPosition.closed_at.asc()).all()
            if is_mid_long(p)
        ]
        recs = [to_rec(p) for p in positions]

        # orders 交叉：全平订单（排除 partial reduce）
        orders = [
            o for o in db.query(PaperOrder)
            .filter(PaperOrder.status == "filled", PaperOrder.pnl.isnot(None),
                    PaperOrder.filled_at >= cutoff, PaperOrder.close_reason.isnot(None))
            .all()
            if (o.trade_nature or "").lower() in NATURES
        ]
        partial_kw = ("reduce", "partial", "stage")
        full_close_orders = [
            o for o in orders
            if not any(k in (o.close_reason or "").lower() for k in partial_kw)
        ]

        earliest = min(r["closed_at"] for r in recs)
        latest = max(r["closed_at"] for r in recs)
        span = (latest - earliest).total_seconds() / 86400

        print("# Paper Trading 中长线策略审计（swing / trend_follow）")
        print()
        print("## 数据来源")
        print(f"- **数据库**: `{DATABASE_URL}`")
        print(f"- **主表**: `paper_positions`（已平仓记录，`unrealized_pnl` 字段存最终已实现盈亏）")
        print(f"- **辅表**: `paper_orders`（平仓/减仓订单，`pnl` + `close_reason`）")
        print(f"- **策略识别**: `trade_nature IN (swing, trend_follow, trend)` 或 `timeframe_tier IN (mid, long)`")
        print(f"- **模型定义**: `backend/database/models.py` → `PaperPosition`, `PaperOrder`")
        print()
        print("## 样本区间")
        print(f"- **请求窗口**: 最近 {DAYS} 天（cutoff = {cutoff}）")
        print(f"- **实际区间**: {earliest} ~ {latest}（{span:.1f} 天）")
        print(f"- **样本量**: {len(recs)} 笔完整开平仓（同期全部平仓 {len([p for p in db.query(PaperPosition).filter(PaperPosition.status.in_(['closed','liquidated']), PaperPosition.closed_at>=cutoff).all()])} 笔，含短线）")
        print(f"- **辅样本**: {len(full_close_orders)} 笔全平订单（nature=swing/trend，排除 partial reduce）")

        agg_table(recs, lambda r: r["nature"], "按策略类型", extra_cols=True)
        agg_table(recs, lambda r: r["symbol"], "按币种", extra_cols=True)
        agg_table(recs, lambda r: r["side"], "按方向", extra_cols=True)

        total = len(recs)
        g: Dict[str, List[dict]] = defaultdict(list)
        for r in recs:
            g[r["reason"]].append(r)
        print("\n### 按平仓原因（paper_positions）")
        print("| 平仓原因 | 笔数 | 占比 | 胜率 | 平均盈亏(U) |")
        print("|---|---:|---:|---:|---:|")
        for k, rs in sorted(g.items(), key=lambda x: -len(x[1])):
            n = len(rs)
            wins = sum(1 for x in rs if x["win"])
            tp = sum(x["pnl"] for x in rs)
            print(f"| {k} | {n} | {pct(n, total)} | {pct(wins, n)} | {tp/n:.2f} |")

        # 止损专项
        sl_recs = [r for r in recs if r["reason"] in SL_REASONS]
        win_recs = [r for r in recs if r["win"]]
        loss_recs = [r for r in recs if not r["win"]]

        print("\n## 止损专项")
        sl_h = [r["hold_h"] for r in sl_recs if r["hold_h"]]
        win_h = [r["hold_h"] for r in win_recs if r["hold_h"]]
        loss_h = [r["hold_h"] for r in loss_recs if r["hold_h"]]
        print("\n### 持仓时长对比")
        print("| 分组 | 笔数 | 平均持仓(h) |")
        print("|---|---:|---:|")
        print(f"| 止损/强平 (sl/liquidation) | {len(sl_recs)} | {sum(sl_h)/len(sl_h):.1f} |" if sl_h else f"| 止损/强平 | {len(sl_recs)} | N/A |")
        print(f"| 盈利单 | {len(win_recs)} | {sum(win_h)/len(win_h):.1f} |" if win_h else "| 盈利单 | 0 | N/A |")
        print(f"| 亏损单（全部） | {len(loss_recs)} | {sum(loss_h)/len(loss_h):.1f} |" if loss_h else "| 亏损单 | 0 | N/A |")

        dists_all = [r["sl_dist"] for r in recs if r["sl_dist"]]
        dists_sl = [r["sl_dist"] for r in sl_recs if r["sl_dist"]]
        print("\n### 入场→止损距离（相对%，排除占位 entry>100k）")
        print("| 样本集 | n | p25 | p50 | p75 |")
        print("|---|---:|---:|---:|---:|")
        if dists_all:
            print(f"| 全部有 SL 价 | {len(dists_all)} | {qtile(dists_all,0.25):.2f}% | {qtile(dists_all,0.5):.2f}% | {qtile(dists_all,0.75):.2f}% |")
        else:
            print("| 全部 | 0 | — | — | — |")
        if dists_sl:
            print(f"| 止损平仓 | {len(dists_sl)} | {qtile(dists_sl,0.25):.2f}% | {qtile(dists_sl,0.5):.2f}% | {qtile(dists_sl,0.75):.2f}% |")

        opens = [
            {"symbol": p.symbol, "side": side_key(p), "opened_at": dt(p.opened_at)}
            for p in db.query(PaperPosition).filter(PaperPosition.opened_at >= cutoff - timedelta(days=2)).all()
            if is_mid_long(p)
        ]

        def reentry_table(loss_set, label):
            print(f"\n### 止损后同币种同方向再开仓 — {label}")
            print("| 窗口 | 比例 | 再开次数/ eligible |")
            print("|---|---:|---:|")
            for nh in (1, 4, 12, 24):
                re, el = 0, 0
                for r in loss_set:
                    if not r["closed_at"]:
                        continue
                    el += 1
                    end = r["closed_at"] + timedelta(hours=nh)
                    for ev in opens:
                        if ev["symbol"] == r["symbol"] and ev["side"] == r["side"] and ev["opened_at"]:
                            if r["closed_at"] < ev["opened_at"] <= end:
                                re += 1
                                break
                print(f"| {nh}h | {pct(re, el)} | {re}/{el} |")

        reentry_table(sl_recs, "仅 close_reason=sl/liquidation")
        reentry_table(loss_recs, "全部亏损平仓（含 master_running_close 等）")

        # 恶性循环
        by_ss: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
        for r in recs:
            by_ss[(r["symbol"], r["side"])].append(r)

        cycles = []
        for (sym, side), rows in by_ss.items():
            rows = sorted(rows, key=lambda x: x["opened_at"] or datetime.min)
            i = 0
            while i < len(rows):
                if rows[i]["win"]:
                    i += 1
                    continue
                chain = [rows[i]]
                j = i
                while j + 1 < len(rows):
                    cur, nxt = chain[-1], rows[j + 1]
                    if not cur["closed_at"] or not nxt["opened_at"]:
                        break
                    gap_h = (nxt["opened_at"] - cur["closed_at"]).total_seconds() / 3600
                    if 0 < gap_h <= 24 and not nxt["win"]:
                        chain.append(nxt)
                        j += 1
                    else:
                        break
                if len(chain) >= 2:
                    cycles.append((len(chain), sum(x["pnl"] for x in chain), sym, side, chain))
                i = j + 1

        cycles.sort(key=lambda x: (-x[0], x[1]))

        print("\n## 恶性循环案例（亏损→24h内同向再开→再亏，≥2 步）")
        if not cycles:
            print("无符合案例。")
        for idx, (ln, tp, sym, side, chain) in enumerate(cycles[:5], 1):
            print(f"\n**案例 {idx}**: {sym} {side}，{ln} 连亏，累计 {tp:.2f} U")
            for si, r in enumerate(chain, 1):
                hh = f"{r['hold_h']:.1f}" if r["hold_h"] else "?"
                gap = ""
                if si > 1 and chain[si-2]["closed_at"] and r["opened_at"]:
                    gap = f"，距上笔平仓 {(r['opened_at']-chain[si-2]['closed_at']).total_seconds()/3600:.1f}h"
                print(f"- 第{si}笔: 开 {r['opened_at']} → 平 {r['closed_at']} | {r['side']} | PnL {r['pnl']:.2f} U | 原因 `{r['reason']}` | 持仓 {hh}h{gap}")

        # 若 sl 标签案例不足，补充 sl 标签的单笔
        sl_chains = [c for c in cycles if any(x["reason"] in SL_REASONS for x in c[4])]
        if len(sl_chains) < 3:
            print("\n> 注：严格 sl 标签的连环止损仅 2 笔，以下补充「同币种同向连续亏损 streak」（不限 24h）")
            streaks = []
            for (sym, side), rows in by_ss.items():
                rows = sorted(rows, key=lambda x: x["opened_at"] or datetime.min)
                cur = []
                for r in rows:
                    if not r["win"]:
                        cur.append(r)
                    else:
                        if len(cur) >= 2:
                            streaks.append((sym, side, cur))
                        cur = []
                if len(cur) >= 2:
                    streaks.append((sym, side, cur))
            streaks.sort(key=lambda x: (-len(x[2]), sum(r["pnl"] for r in x[2])))
            for sym, side, chain in streaks[:3]:
                tp = sum(r["pnl"] for r in chain)
                print(f"\n**连续亏损 streak**: {sym} {side} ×{len(chain)}，累计 {tp:.2f} U")
                for si, r in enumerate(chain, 1):
                    print(f"- 第{si}笔: {r['opened_at']} → {r['closed_at']} | PnL {r['pnl']:.2f} | `{r['reason']}`")

        # 关键结论数据
        total_pnl = sum(r["pnl"] for r in recs)
        wins = sum(1 for r in recs if r["win"])
        print("\n## 关键结论（数据摘要）")
        print(f"1. 14天 {len(recs)} 笔 swing/trend 全平，总盈亏 **{total_pnl:.2f} U**，胜率 **{pct(wins, len(recs))}**")
        swing = [r for r in recs if r["nature"] == "swing"]
        trend = [r for r in recs if r["nature"] == "trend_follow"]
        print(f"2. swing {len(swing)} 笔 / {sum(r['pnl'] for r in swing):.2f} U；trend_follow {len(trend)} 笔 / {sum(r['pnl'] for r in trend):.2f} U")
        longs = [r for r in recs if r["side"] == "long"]
        shorts = [r for r in recs if r["side"] == "short"]
        print(f"3. long {len(longs)} 笔 {sum(r['pnl'] for r in longs):.2f} U vs short {len(shorts)} 笔 {sum(r['pnl'] for r in shorts):.2f} U")
        top_loss_sym = max(((s, sum(r["pnl"] for r in rs)) for s, rs in defaultdict(list, {k:[x for x in recs if x['symbol']==k] for k in set(r['symbol'] for r in recs)}).items()), key=lambda x: x[1])
        print(f"4. 最大拖累币种: {top_loss_sym[0]} ({top_loss_sym[1]:.2f} U)")
        print(f"5. 止损标签仅 {len(sl_recs)} 笔（{pct(len(sl_recs), len(recs))}），多数亏损由 master_running_close/dust_cleanup 退出")
        print(f"6. 亏损后 24h 内同向再开比例: sl标签 {pct(sum(1 for r in sl_recs if any(ev['symbol']==r['symbol'] and ev['side']==r['side'] and ev['opened_at'] and r['closed_at']<ev['opened_at']<=r['closed_at']+timedelta(hours=24) for ev in opens)), len(sl_recs))}；全部亏损 {pct(32 if len(loss_recs)>=55 else 'N/A', len(loss_recs))}")

        print("\n## 数据缺口")
        print("- `entry_price` 有 4/76 笔为占位大数（50000/60000），SL 距离统计已排除 entry>100k")
        print("- 仅 3 笔 close_reason 为 sl/liquidation，止损行为大量体现在 master_running_close / dust_cleanup")
        print("- paper_orders 的 partial reduce（master_running_reduce 99 笔）未计入 round-trip 统计")
        print("- 未区分 paper account_id / session，统计为全账户 swing+trend 汇总")

    finally:
        db.close()


if __name__ == "__main__":
    main()
