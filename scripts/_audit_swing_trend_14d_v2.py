"""Paper swing/trend 审计 v2 — orders+positions 双源，修正 PnL。"""
from __future__ import annotations

import os, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

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
LOSS_REASONS_SL = {"sl", "stop_loss", "liquidation", "liquidated"}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dt(v):
    if v is None:
        return None
    return v.replace(tzinfo=None) if getattr(v, "tzinfo", None) else v


def is_mid_long_pos(p):
    nat = (p.trade_nature or "").lower()
    tier = (p.timeframe_tier or "").lower()
    return nat in NATURES or tier in TIERS


def is_mid_long_order(o):
    nat = (o.trade_nature or "").lower()
    return nat in NATURES


def pos_pnl_from_orders(db, pos_id, symbol, closed_at) -> Optional[float]:
    """尝试从 paper_orders 汇总该仓位平仓 pnl。"""
    q = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.status == "filled",
            PaperOrder.pnl.isnot(None),
            PaperOrder.symbol == symbol,
        )
    )
    if closed_at:
        lo = closed_at - timedelta(hours=48)
        hi = closed_at + timedelta(hours=2)
        q = q.filter(PaperOrder.filled_at >= lo, PaperOrder.filled_at <= hi)
    orders = q.all()
    if not orders:
        return None
    return sum(float(o.pnl or 0) for o in orders)


def pos_pnl(p: PaperPosition) -> float:
    pr = float(p.partial_realized_pnl or 0)
    fee = float(p.partial_fee_paid or 0)
    if p.close_price and p.entry_price and p.size:
        ep, cp, sz = float(p.entry_price), float(p.close_price), float(p.size)
        gross = (cp - ep) * sz if (p.side or "").lower() in ("long", "buy") else (ep - cp) * sz
        return gross + pr - fee
    return pr - fee


def hold_h(p):
    o, c = dt(p.opened_at), dt(p.closed_at)
    return (c - o).total_seconds() / 3600 if o and c else None


def sl_dist(p):
    if not (p.entry_price and p.sl_price):
        return None
    ep, sl = float(p.entry_price), float(p.sl_price)
    if ep <= 0:
        return None
    return abs(ep - sl) / ep * 100


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


def pct(n, d):
    return f"{n/d*100:.1f}%" if d else "0.0%"


def nature_key(p):
    nat = (p.trade_nature or "").lower()
    if nat == "swing":
        return "swing"
    if nat in ("trend_follow", "trend"):
        return "trend_follow"
    tier = (p.timeframe_tier or "").lower()
    if tier == "mid":
        return "swing"
    if tier == "long":
        return "trend_follow"
    return "other"


def main():
    db = SessionLocal()
    cutoff = utcnow() - timedelta(days=DAYS)
    try:
        positions = [
            p for p in db.query(PaperPosition)
            .filter(PaperPosition.status.in_(["closed", "liquidated"]),
                    PaperPosition.closed_at >= cutoff)
            .order_by(PaperPosition.closed_at.asc()).all()
            if is_mid_long_pos(p)
        ]

        close_orders = [
            o for o in db.query(PaperOrder)
            .filter(PaperOrder.status == "filled", PaperOrder.pnl.isnot(None),
                    PaperOrder.filled_at >= cutoff,
                    PaperOrder.close_reason.isnot(None))
            .order_by(PaperOrder.filled_at.asc()).all()
            if is_mid_long_order(o)
        ]

        # position records with dual pnl
        recs = []
        for p in positions:
            calc = pos_pnl(p)
            recs.append({
                "id": p.id, "symbol": p.symbol, "side": (p.side or "").lower(),
                "nature": nature_key(p),
                "trade_nature": (p.trade_nature or "").lower(),
                "tier": (p.timeframe_tier or "").lower(),
                "reason": (p.close_reason or "unknown").lower(),
                "pnl": calc,
                "win": calc >= 0,
                "hold_h": hold_h(p),
                "opened_at": dt(p.opened_at), "closed_at": dt(p.closed_at),
                "entry": float(p.entry_price or 0),
                "close_px": float(p.close_price) if p.close_price else None,
                "sl": float(p.sl_price) if p.sl_price else None,
                "tp": float(p.tp_price) if p.tp_price else None,
                "sl_dist": sl_dist(p),
                "margin": float(p.margin or 0),
                "size": float(p.size or 0),
                "leverage": float(p.leverage or 1),
            })

        # orders-based stats
        ord_recs = [{
            "symbol": o.symbol, "side": (o.side or "").lower(),
            "nature": (o.trade_nature or "").lower(),
            "reason": (o.close_reason or "unknown").lower(),
            "pnl": float(o.pnl or 0),
            "win": float(o.pnl or 0) >= 0,
            "filled_at": dt(o.filled_at),
            "sl": float(o.sl_price) if o.sl_price else None,
            "entry": float(o.entry_price or o.filled_price or 0),
        } for o in close_orders]

        print("=== DATA SOURCE ===")
        print(f"DB: {DATABASE_URL}")
        print(f"Tables: paper_positions, paper_orders")
        print(f"Nature filter: {NATURES}; Tier filter: {TIERS}")
        print(f"Window: last {DAYS}d from {cutoff}")

        if recs:
            print(f"Positions closed (mid/long): {len(recs)}")
            print(f"Range: {min(r['closed_at'] for r in recs)} ~ {max(r['closed_at'] for r in recs)}")
        print(f"Close orders (mid/long nature): {len(ord_recs)}")

        # PnL sanity: show top abs pnl
        print("\n=== PNL SANITY TOP5 (positions) ===")
        for r in sorted(recs, key=lambda x: abs(x["pnl"]), reverse=True)[:5]:
            print(f"  id={r['id']} {r['symbol']} {r['side']} pnl={r['pnl']:.2f} margin={r['margin']:.2f} "
                  f"size={r['size']:.4f} entry={r['entry']} close={r['close_px']} lev={r['leverage']} reason={r['reason']}")

        print("\n=== PNL SANITY TOP5 (orders) ===")
        for r in sorted(ord_recs, key=lambda x: abs(x["pnl"]), reverse=True)[:5]:
            print(f"  {r['symbol']} pnl={r['pnl']:.2f} reason={r['reason']} nature={r['nature']}")

        def table_by(key_fn, rows, name):
            g = defaultdict(list)
            for r in rows:
                g[key_fn(r)].append(r)
            print(f"\n=== {name} ===")
            print("| 维度 | 笔数 | 胜率 | 总盈亏 | 平均盈亏 | 平均持仓h | 最大连亏 |")
            print("|---|---:|---:|---:|---:|---:|---:|")
            for k in sorted(g.keys()):
                rs = sorted(g[k], key=lambda x: x.get("closed_at") or x.get("filled_at") or datetime.min)
                pnls = [x["pnl"] for x in rs]
                n = len(rs)
                wins = sum(1 for x in rs if x["win"])
                tp = sum(pnls)
                holds = [x["hold_h"] for x in rs if x.get("hold_h")]
                ah = sum(holds)/len(holds) if holds else 0
                mcl = max_consec_loss(pnls)
                print(f"| {k} | {n} | {pct(wins,n)} | {tp:.2f} | {tp/n:.2f} | {ah:.1f} | {mcl} |")

        table_by(lambda r: r["nature"], recs, "BY NATURE (positions)")
        table_by(lambda r: r["symbol"], recs, "BY SYMBOL (positions)")
        table_by(lambda r: r["side"], recs, "BY SIDE (positions)")

        # close reason from positions
        g = defaultdict(list)
        for r in recs:
            g[r["reason"]].append(r)
        total = len(recs)
        print("\n=== BY CLOSE REASON (positions) ===")
        print("| 原因 | 笔数 | 占比 | 胜率 | 平均盈亏 |")
        print("|---|---:|---:|---:|---:|")
        for k, rs in sorted(g.items(), key=lambda x: -len(x[1])):
            n = len(rs)
            wins = sum(1 for x in rs if x["win"])
            tp = sum(x["pnl"] for x in rs)
            print(f"| {k} | {n} | {pct(n,total)} | {pct(wins,n)} | {tp/n:.2f} |")

        # close reason from orders
        g2 = defaultdict(list)
        for r in ord_recs:
            g2[r["reason"]].append(r)
        total2 = len(ord_recs)
        print("\n=== BY CLOSE REASON (orders) ===")
        print("| 原因 | 笔数 | 占比 | 胜率 | 平均盈亏 |")
        print("|---|---:|---:|---:|---:|")
        for k, rs in sorted(g2.items(), key=lambda x: -len(x[1])):
            n = len(rs)
            wins = sum(1 for x in rs if x["win"])
            tp = sum(x["pnl"] for x in rs)
            print(f"| {k} | {n} | {pct(n,total2)} | {pct(wins,n)} | {tp/n:.2f} |")

        # SL analysis - expand to loss reasons
        sl_pos = [r for r in recs if r["reason"] in LOSS_REASONS_SL]
        win_pos = [r for r in recs if r["win"]]
        loss_pos = [r for r in recs if not r["win"]]

        print("\n=== SL / LOSS SPECIAL (positions) ===")
        sl_h = [r["hold_h"] for r in sl_pos if r["hold_h"]]
        win_h = [r["hold_h"] for r in win_pos if r["hold_h"]]
        loss_h = [r["hold_h"] for r in loss_pos if r["hold_h"]]
        print(f"labeled_sl_n={len(sl_pos)} sl_avg_hold={sum(sl_h)/len(sl_h) if sl_h else 'N/A'}")
        print(f"win_avg_hold={sum(win_h)/len(win_h) if win_h else 'N/A'}")
        print(f"loss_avg_hold={sum(loss_h)/len(loss_h) if loss_h else 'N/A'}")

        # SL distance for all positions with sl_price set
        dists = [r["sl_dist"] for r in recs if r["sl_dist"]]
        if dists:
            print(f"sl_dist_pct ALL n={len(dists)} p25={qtile(dists,0.25):.2f} p50={qtile(dists,0.5):.2f} p75={qtile(dists,0.75):.2f}")
        sl_dists = [r["sl_dist"] for r in sl_pos if r["sl_dist"]]
        if sl_dists:
            print(f"sl_dist_pct SL-only n={len(sl_dists)} p25={qtile(sl_dists,0.25):.2f} p50={qtile(sl_dists,0.5):.2f} p75={qtile(sl_dists,0.75):.2f}")

        # re-entry after ANY loss close (not just sl)
        opens = [
            {"symbol": p.symbol, "side": (p.side or "").lower(), "opened_at": dt(p.opened_at)}
            for p in db.query(PaperPosition).filter(PaperPosition.opened_at >= cutoff - timedelta(days=1)).all()
            if is_mid_long_pos(p)
        ]
        for label, loss_set in [("SL_labeled", sl_pos), ("ANY_loss", loss_pos)]:
            print(f"\n--- Re-entry after {label} ---")
            for nh in (1, 4, 12, 24):
                re, el = 0, 0
                for r in loss_set:
                    if not r["closed_at"]:
                        continue
                    el += 1
                    end = r["closed_at"] + timedelta(hours=nh)
                    for ev in opens:
                        if ev["symbol"]==r["symbol"] and ev["side"]==r["side"] and ev["opened_at"]:
                            if r["closed_at"] < ev["opened_at"] <= end:
                                re += 1
                                break
                print(f"  {nh}h: {pct(re,el)} ({re}/{el})")

        # vicious cycles: loss close -> reopen same dir within 24h -> loss close (repeat)
        by_ss = defaultdict(list)
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
                    cur, nxt = chain[-1], rows[j+1]
                    if not cur["closed_at"] or not nxt["opened_at"]:
                        break
                    if cur["closed_at"] < nxt["opened_at"] <= cur["closed_at"] + timedelta(hours=24):
                        chain.append(nxt)
                        j += 1
                        if not nxt["win"]:
                            j += 1
                            continue
                        break
                    break
                loss_chain = [x for x in chain if not x["win"]]
                if len(loss_chain) >= 2:
                    cycles.append((len(loss_chain), sum(x["pnl"] for x in loss_chain), sym, side, loss_chain))
                i = max(i + 1, j + 1)

        cycles.sort(key=lambda x: (-x[0], x[1]))
        print("\n=== VICIOUS CYCLES (loss->reopen<=24h->loss, >=2 steps) ===")
        if not cycles:
            print("NONE found with 24h window")
            # relax to 48h
            for (sym, side), rows in by_ss.items():
                rows = sorted(rows, key=lambda x: x["opened_at"] or datetime.min)
                for i, r in enumerate(rows):
                    if r["win"]:
                        continue
                    for j in range(i+1, len(rows)):
                        nxt = rows[j]
                        if r["closed_at"] and nxt["opened_at"] and r["closed_at"] < nxt["opened_at"] <= r["closed_at"]+timedelta(hours=48) and not nxt["win"]:
                            cycles.append((2, r["pnl"]+nxt["pnl"], sym, side, [r, nxt]))
            cycles = sorted(set((c[0], c[1], c[2], c[3], tuple(x["id"] for x in c[4])) for c in cycles))
            print(f"48h pairwise loss chains: {len(cycles)}")

        shown = 0
        for c in cycles[:5]:
            if shown >= 5:
                break
            ln, tp, sym, side, chain = c[0], c[1], c[2], c[3], c[4]
            if isinstance(chain, tuple):
                continue
            shown += 1
            print(f"\nCase {shown}: {sym} {side} steps={ln} total_pnl={tp:.2f}")
            for si, r in enumerate(chain, 1):
                print(f"  {si}. open={r['opened_at']} close={r['closed_at']} pnl={r['pnl']:.2f} "
                      f"reason={r['reason']} hold_h={r['hold_h']:.1f if r['hold_h'] else '?'}")

        # Also list consecutive same-symbol same-side losses (any gap)
        print("\n=== CONSECUTIVE LOSS STREAKS (same symbol+side, any gap) ===")
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
        for sym, side, chain in streaks[:5]:
            print(f"\n{sym} {side} streak={len(chain)} total={sum(r['pnl'] for r in chain):.2f}")
            for r in chain:
                gap = ""
                print(f"  open={r['opened_at']} close={r['closed_at']} pnl={r['pnl']:.2f} reason={r['reason']}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
