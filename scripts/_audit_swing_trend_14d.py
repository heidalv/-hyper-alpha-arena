"""Paper trading swing/trend 近14天全量审计统计。"""
from __future__ import annotations

import os
import sys
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
MID_LONG_NATURES = {"swing", "trend_follow", "trend"}
MID_LONG_TIERS = {"mid", "long"}


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo else v
    return None


def _position_pnl(p: PaperPosition) -> float:
    """估算已平仓净盈亏：优先用 close_price 计算 gross + partial，否则用 partial。"""
    pr = float(p.partial_realized_pnl or 0)
    fee = float(p.partial_fee_paid or 0)
    if p.close_price and p.entry_price and p.size:
        ep, cp, sz = float(p.entry_price), float(p.close_price), float(p.size)
        if (p.side or "").lower() in ("long", "buy"):
            gross = (cp - ep) * sz
        else:
            gross = (ep - cp) * sz
        return gross + pr - fee
    return pr - fee


def _hold_hours(p: PaperPosition) -> Optional[float]:
    o, c = _parse_dt(p.opened_at), _parse_dt(p.closed_at)
    if o and c:
        return (c - o).total_seconds() / 3600.0
    return None


def _sl_distance_pct(p: PaperPosition) -> Optional[float]:
    if not (p.entry_price and p.sl_price):
        return None
    ep, sl = float(p.entry_price), float(p.sl_price)
    if ep <= 0:
        return None
    side = (p.side or "").lower()
    if side in ("long", "buy"):
        return abs(ep - sl) / ep * 100.0
    return abs(sl - ep) / ep * 100.0


def _max_consecutive_losses(pnls: List[float]) -> int:
    best = cur = 0
    for x in pnls:
        if x < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{n / total * 100:.1f}%"


def _fmt(v: float, d: int = 2) -> str:
    return f"{v:.{d}f}"


def _quantile(vals: List[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    idx = (len(s) - 1) * q
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _is_mid_long(p: PaperPosition) -> bool:
    nat = (p.trade_nature or "").strip().lower()
    tier = (p.timeframe_tier or "").strip().lower()
    if nat in MID_LONG_NATURES:
        return True
    if tier in MID_LONG_TIERS:
        return True
    return False


def _nature_bucket(p: PaperPosition) -> str:
    nat = (p.trade_nature or "").strip().lower()
    if nat in ("swing",):
        return "swing"
    if nat in ("trend_follow", "trend"):
        return "trend_follow"
    tier = (p.timeframe_tier or "").strip().lower()
    if tier == "mid":
        return "swing(tier=mid)"
    if tier == "long":
        return "trend_follow(tier=long)"
    return nat or tier or "unknown"


def main() -> None:
    db = SessionLocal()
    cutoff = _utcnow_naive() - timedelta(days=DAYS)
    try:
        all_closed = (
            db.query(PaperPosition)
            .filter(
                PaperPosition.status.in_(["closed", "liquidated"]),
                PaperPosition.closed_at.isnot(None),
                PaperPosition.closed_at >= cutoff,
            )
            .order_by(PaperPosition.closed_at.asc())
            .all()
        )

        positions = [p for p in all_closed if _is_mid_long(p)]

        # 也查 orders 作为交叉验证
        orders = (
            db.query(PaperOrder)
            .filter(
                PaperOrder.status == "filled",
                PaperOrder.pnl.isnot(None),
                PaperOrder.filled_at >= cutoff,
            )
            .order_by(PaperOrder.filled_at.asc())
            .all()
        )
        mid_long_orders = [
            o
            for o in orders
            if (o.trade_nature or "").lower() in MID_LONG_NATURES
            or o.close_reason  # 平仓单
        ]

        if not positions:
            print("NO_DATA")
            print(f"DATABASE_URL={DATABASE_URL}")
            print(f"cutoff={cutoff.isoformat()}")
            print(f"all_closed_14d={len(all_closed)}")
            return

        earliest = min(_parse_dt(p.closed_at) for p in positions if p.closed_at)
        latest = max(_parse_dt(p.closed_at) for p in positions if p.closed_at)
        span_days = (latest - earliest).total_seconds() / 86400.0 if earliest and latest else 0

        records: List[Dict[str, Any]] = []
        for p in positions:
            pnl = _position_pnl(p)
            records.append(
                {
                    "id": p.id,
                    "symbol": p.symbol,
                    "side": (p.side or "").lower(),
                    "nature": _nature_bucket(p),
                    "trade_nature": (p.trade_nature or "").lower(),
                    "tier": (p.timeframe_tier or "").lower(),
                    "close_reason": (p.close_reason or "unknown").lower(),
                    "pnl": pnl,
                    "win": pnl >= 0,
                    "hold_h": _hold_hours(p),
                    "opened_at": _parse_dt(p.opened_at),
                    "closed_at": _parse_dt(p.closed_at),
                    "entry": float(p.entry_price or 0),
                    "close": float(p.close_price or 0) if p.close_price else None,
                    "sl": float(p.sl_price) if p.sl_price else None,
                    "tp": float(p.tp_price) if p.tp_price else None,
                    "sl_dist_pct": _sl_distance_pct(p),
                }
            )

        # ── 按策略类型 ──
        by_nature: Dict[str, List[Dict]] = defaultdict(list)
        for r in records:
            # 归并 swing vs trend
            if "swing" in r["nature"]:
                key = "swing"
            elif "trend" in r["nature"]:
                key = "trend_follow"
            else:
                key = r["nature"]
            by_nature[key].append(r)

        # ── 按币种 ──
        by_symbol: Dict[str, List[Dict]] = defaultdict(list)
        for r in records:
            by_symbol[r["symbol"]].append(r)

        # ── 按方向 ──
        by_side: Dict[str, List[Dict]] = defaultdict(list)
        for r in records:
            side = r["side"]
            if side in ("buy",):
                side = "long"
            elif side in ("sell",):
                side = "short"
            by_side[side].append(r)

        # ── 按平仓原因 ──
        by_reason: Dict[str, List[Dict]] = defaultdict(list)
        for r in records:
            by_reason[r["close_reason"]].append(r)

        def agg(rows: List[Dict], sort_key=None) -> List[str]:
            if not rows:
                return []
            n = len(rows)
            wins = sum(1 for x in rows if x["win"])
            total_pnl = sum(x["pnl"] for x in rows)
            holds = [x["hold_h"] for x in rows if x["hold_h"] is not None]
            avg_hold = sum(holds) / len(holds) if holds else 0
            mcl = _max_consecutive_losses([x["pnl"] for x in sorted(rows, key=lambda x: x["closed_at"] or datetime.min)])
            return [str(n), _pct(wins, n), _fmt(total_pnl), _fmt(total_pnl / n), _fmt(avg_hold, 1), str(mcl)]

        print("=== META ===")
        print(f"DATABASE_URL={DATABASE_URL}")
        print(f"TABLE=paper_positions (primary), paper_orders (cross-ref)")
        print(f"FILTER=trade_nature in {MID_LONG_NATURES} OR timeframe_tier in {MID_LONG_TIERS}")
        print(f"REQUESTED_DAYS={DAYS}")
        print(f"ACTUAL_RANGE={earliest} ~ {latest} ({span_days:.1f} calendar days)")
        print(f"SAMPLE_COUNT={len(records)} closed positions")
        print(f"ALL_CLOSED_14D={len(all_closed)} (incl non mid/long)")
        print(f"ORDERS_14D_FILLED={len(orders)} | mid_long_close_orders={len([o for o in orders if o.pnl is not None and (o.trade_nature or '').lower() in MID_LONG_NATURES])}")

        print("\n=== BY STRATEGY TYPE ===")
        print("| 策略类型 | 笔数 | 胜率 | 总盈亏 | 平均盈亏 | 平均持仓(h) | 最大连亏 |")
        print("|---|---:|---:|---:|---:|---:|---:|")
        for key in sorted(by_nature.keys()):
            rows = by_nature[key]
            a = agg(rows)
            print(f"| {key} | {' | '.join(a)} |")

        print("\n=== BY SYMBOL ===")
        print("| 币种 | 笔数 | 胜率 | 总盈亏 | 平均盈亏 |")
        print("|---|---:|---:|---:|---:|")
        for sym, rows in sorted(by_symbol.items(), key=lambda x: -len(x[1])):
            n = len(rows)
            wins = sum(1 for x in rows if x["win"])
            tp = sum(x["pnl"] for x in rows)
            print(f"| {sym} | {n} | {_pct(wins, n)} | {_fmt(tp)} | {_fmt(tp/n)} |")

        print("\n=== BY DIRECTION ===")
        print("| 方向 | 笔数 | 胜率 | 总盈亏 | 平均盈亏 |")
        print("|---|---:|---:|---:|---:|")
        for side in ("long", "short"):
            rows = by_side.get(side, [])
            if not rows:
                continue
            n = len(rows)
            wins = sum(1 for x in rows if x["win"])
            tp = sum(x["pnl"] for x in rows)
            print(f"| {side} | {n} | {_pct(wins, n)} | {_fmt(tp)} | {_fmt(tp/n)} |")

        print("\n=== BY CLOSE REASON ===")
        print("| 平仓原因 | 笔数 | 占比 | 胜率 | 平均盈亏 |")
        print("|---|---:|---:|---:|---:|")
        total_n = len(records)
        for reason, rows in sorted(by_reason.items(), key=lambda x: -len(x[1])):
            n = len(rows)
            wins = sum(1 for x in rows if x["win"])
            tp = sum(x["pnl"] for x in rows)
            print(f"| {reason} | {n} | {_pct(n, total_n)} | {_pct(wins, n)} | {_fmt(tp/n)} |")

        # ── 止损专项 ──
        sl_records = [r for r in records if r["close_reason"] in ("sl", "stop_loss", "liquidated")]
        win_records = [r for r in records if r["win"]]

        print("\n=== STOP LOSS SPECIAL ===")
        sl_holds = [r["hold_h"] for r in sl_records if r["hold_h"] is not None]
        win_holds = [r["hold_h"] for r in win_records if r["hold_h"] is not None]
        print(f"SL_avg_hold_h={_fmt(sum(sl_holds)/len(sl_holds), 1) if sl_holds else 'N/A'}")
        print(f"WIN_avg_hold_h={_fmt(sum(win_holds)/len(win_holds), 1) if win_holds else 'N/A'}")

        sl_dists = [r["sl_dist_pct"] for r in sl_records if r["sl_dist_pct"] is not None]
        if sl_dists:
            print(f"SL_distance_pct_p25={_fmt(_quantile(sl_dists, 0.25) or 0)}")
            print(f"SL_distance_pct_p50={_fmt(_quantile(sl_dists, 0.50) or 0)}")
            print(f"SL_distance_pct_p75={_fmt(_quantile(sl_dists, 0.75) or 0)}")
            print(f"SL_distance_pct_n={len(sl_dists)}")
        else:
            print("SL_distance_pct=NO_DATA (missing sl_price/entry_price)")

        # 止损后 N 小时内同币种同方向再开仓
        opens = (
            db.query(PaperPosition)
            .filter(
                PaperPosition.opened_at.isnot(None),
                PaperPosition.opened_at >= cutoff - timedelta(days=1),
            )
            .order_by(PaperPosition.opened_at.asc())
            .all()
        )
        open_events = [
            {
                "symbol": p.symbol,
                "side": (p.side or "").lower(),
                "opened_at": _parse_dt(p.opened_at),
                "nature_ok": _is_mid_long(p),
            }
            for p in opens
            if _is_mid_long(p)
        ]

        for n_h in (1, 4, 12, 24):
            reentry = 0
            eligible = 0
            for r in sl_records:
                if not r["closed_at"]:
                    continue
                eligible += 1
                sym, side, closed = r["symbol"], r["side"], r["closed_at"]
                window_end = closed + timedelta(hours=n_h)
                for ev in open_events:
                    if (
                        ev["symbol"] == sym
                        and ev["side"] == side
                        and ev["opened_at"]
                        and closed < ev["opened_at"] <= window_end
                    ):
                        reentry += 1
                        break
            print(f"REENTRY_WITHIN_{n_h}H={_pct(reentry, eligible)} ({reentry}/{eligible})")

        # ── 恶性循环案例 ──
        print("\n=== VICIOUS CYCLE CASES ===")
        # 按 symbol+side 排序所有记录，找 sl -> open -> sl 链
        sorted_recs = sorted(records, key=lambda x: (x["symbol"], x["side"], x["opened_at"] or datetime.min))
        cycles: List[Tuple[int, List[Dict]]] = []

        by_sym_side: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
        for r in records:
            by_sym_side[(r["symbol"], r["side"])].append(r)
        for (sym, side), rows in by_sym_side.items():
            rows = sorted(rows, key=lambda x: x["opened_at"] or datetime.min)
            chain: List[Dict] = []
            for r in rows:
                if r["close_reason"] in ("sl", "stop_loss", "liquidated"):
                    chain.append(r)
                else:
                    if len(chain) >= 2:
                        cycles.append((len(chain), chain.copy()))
                    chain = []
            if len(chain) >= 2:
                cycles.append((len(chain), chain.copy()))

        # 也找 sl -> (within 24h reopen) -> sl
        detailed_cycles: List[Tuple[int, float, List[Dict]]] = []
        for (sym, side), rows in by_sym_side.items():
            rows = sorted(rows, key=lambda x: x["opened_at"] or datetime.min)
            for i, r in enumerate(rows):
                if r["close_reason"] not in ("sl", "stop_loss", "liquidated"):
                    continue
                if not r["closed_at"]:
                    continue
                for j in range(i + 1, len(rows)):
                    nxt = rows[j]
                    if nxt["opened_at"] and r["closed_at"] < nxt["opened_at"] <= r["closed_at"] + timedelta(hours=24):
                        if nxt["close_reason"] in ("sl", "stop_loss", "liquidated"):
                            seq = [r, nxt]
                            # extend chain
                            last = nxt
                            for k in range(j + 1, len(rows)):
                                nxt2 = rows[k]
                                if (
                                    last["closed_at"]
                                    and nxt2["opened_at"]
                                    and last["closed_at"] < nxt2["opened_at"] <= last["closed_at"] + timedelta(hours=24)
                                    and nxt2["close_reason"] in ("sl", "stop_loss", "liquidated")
                                ):
                                    seq.append(nxt2)
                                    last = nxt2
                                else:
                                    break
                            total_loss = sum(x["pnl"] for x in seq)
                            detailed_cycles.append((len(seq), total_loss, seq))
                        break

        detailed_cycles.sort(key=lambda x: (x[0], x[1]))
        detailed_cycles = sorted(detailed_cycles, key=lambda x: (-x[0], x[1]))[:5]

        for idx, (chain_len, total_loss, seq) in enumerate(detailed_cycles, 1):
            print(f"\n--- Case {idx}: {seq[0]['symbol']} {seq[0]['side']} chain_len={chain_len} total_pnl={_fmt(total_loss)} ---")
            for step, r in enumerate(seq, 1):
                print(
                    f"  Step{step}: open={r['opened_at']} close={r['closed_at']} "
                    f"entry={r['entry']} close_px={r['close']} pnl={_fmt(r['pnl'])} "
                    f"reason={r['close_reason']} hold_h={_fmt(r['hold_h'] or 0, 1)} nature={r['nature']}"
                )

        # trade_nature 分布
        print("\n=== RAW NATURE/TIER DISTRIBUTION ===")
        nat_dist: Dict[str, int] = defaultdict(int)
        for r in records:
            nat_dist[f"{r['trade_nature']}|{r['tier']}"] += 1
        for k, v in sorted(nat_dist.items(), key=lambda x: -x[1]):
            print(f"  {k}: {v}")

        print("\n=== CLOSE_REASON RAW ===")
        for k, v in sorted(by_reason.items(), key=lambda x: -x[1]["pnl"]):
            print(f"  {k}: n={len(v)} pnl={_fmt(sum(x['pnl'] for x in v))}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
