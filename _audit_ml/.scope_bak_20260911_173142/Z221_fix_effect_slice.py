# -*- coding: utf-8 -*-
"""[§82 度量工具 2026-09-11] 修复**前后**的中长线出场经济学切片（只读、可日跑）。

目的（用户原话："还是先盈利后大亏离场" / 目标①）：把"修复到底有没有让钱少亏"变成
一个**可复现、可重跑**的度量，而不是凭感觉。口径固定：

  * 净额 = `(close-entry)*size*dir + partial_realized_pnl - partial_fee_paid`
    （与 §70/P3 的净口径唯一真相源一致；`dir = +1 if long/buy else -1`）
  * 分档：`tier ∈ {mid, long}`；按 `close_reason` 归一成**通道**
  * 切点：默认 `2026-09-11 10:19`（P19-B 统一熔断 + P20 重启重建**上线**时刻）

输出：A 总体 before/after ｜ B 逐通道 before/after ｜ C 熔断状态 × 通道可达性
      ｜ D 浮盈回吐（peak 分层） ｜ E 被抑制离场计数（本闸是否真的动过手）

用法：
    python _audit_ml/Z221_fix_effect_slice.py [--boundary "2026-09-11 10:19"] [--days 30] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.exit.channel_breaker_gate import channel_of, is_protected  # noqa: E402
from backend.services.exit.channel_breaker_gate import unified_enabled  # noqa: E402
from sqlalchemy import text  # noqa: E402

NET_EXPR = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
            "else -(close_price-entry_price)*size end) "
            "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")


def _rows(db, boundary: str, days: int):
    return db.execute(text(f"""
        select id, symbol, lower(coalesce(timeframe_tier,'?')) as tier, coalesce(close_reason,'?') as reason,
               {NET_EXPR} as net,
               case when opened_at is null then null
                    else extract(epoch from (closed_at - opened_at))/3600.0 end as hold_h,
               peak_pnl_pct,
               case when entry_price is null or entry_price = 0 then null
                    else (case when lower(side) in ('long','buy')
                               then (close_price-entry_price)/entry_price
                               else (entry_price-close_price)/entry_price end) end as ret_pct
        from paper_positions
        where status = 'closed' and closed_at >= now() - interval '{int(days)} day'
          and lower(coalesce(timeframe_tier,'')) in ('mid','long')
    """)).fetchall(), boundary


def _side(closed_at_iso: str, boundary: str) -> str:
    return "after" if str(closed_at_iso) >= boundary else "before"


def _agg(items):
    n = len(items)
    if not n:
        return {"n": 0, "net": 0.0, "avg": 0.0, "wr": 0.0, "hold": 0.0}
    net = sum(x["net"] for x in items)
    wins = sum(1 for x in items if x["net"] > 0)
    holds = [x["hold"] for x in items if x["hold"] is not None]
    return {"n": n, "net": net, "avg": net / n, "wr": wins / n,
            "hold": (sum(holds) / len(holds)) if holds else 0.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--boundary", default="2026-09-11 10:19", help="切点（本地时间字符串，字典序比较）")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", action="store_true", help="同时写 JSON 快照")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows, _ = _rows(db, args.boundary, args.days)
        closed_map = {r[0]: r[1] for r in db.execute(text(f"""
            select id, to_char(closed_at, 'YYYY-MM-DD HH24:MI:SS') from paper_positions
            where status='closed' and closed_at >= now() - interval '{int(args.days)} day'
        """)).fetchall()}
        suppressed = db.execute(text("""
            select count(*) from position_exit_events where event_type = 'exit_channel_broken'
        """)).scalar()
        sess_events = db.execute(text("""
            select coalesce(sum((
                select count(*) from json_array_elements(event_log) e
                where e->>'event' = 'exit_channel_broken'
            )), 0)
            from full_auto_sessions
        """)).scalar()
    finally:
        db.close()

    items = []
    for r in rows:
        pid, sym, tier, reason, net, hold, peak, ret = r
        items.append({
            "id": pid, "symbol": sym, "tier": tier, "channel": channel_of(reason),
            "reason": reason, "net": float(net or 0),
            # psycopg 对 `extract(epoch ...)` 返回 Decimal ⇒ 必须显式转 float，
            # 否则 JSON 快照会抛 `Object of type Decimal is not JSON serializable`。
            "hold": float(hold) if hold is not None else None,
            "peak": float(peak) if peak is not None else None,
            "ret": float(ret) if ret is not None else None,
            "side": _side(closed_map.get(pid, ""), args.boundary),
        })

    print("=" * 100)
    print(f"中长线出场经济学切片（净口径）｜近 {args.days} 天｜切点 {args.boundary}｜"
          f"统一熔断开关={unified_enabled()}")
    print("=" * 100)

    print("\nA. 总体（mid/long）")
    for label, sel in (("before", lambda x: x["side"] == "before"),
                       ("after ", lambda x: x["side"] == "after"),
                       ("全部  ", lambda x: True)):
        a = _agg([x for x in items if sel(x)])
        print(f"  {label} n={a['n']:<4} 净额=${a['net']:>10.2f} 单笔=${a['avg']:>7.2f} "
              f"胜率={a['wr']*100:>5.1f}% 平均持有={a['hold']:>6.1f}h")

    print("\nB. 逐通道（before → after；净额$ / 笔数 / 胜率）")
    byc = defaultdict(lambda: {"before": [], "after": []})
    for x in items:
        byc[x["channel"]][x["side"]].append(x)
    print(f"  {'通道':28s} {'before':>26s}   {'after':>26s}   熔断/保护")
    for ch, d in sorted(byc.items(), key=lambda kv: _agg(kv[1]["before"])["net"]):
        b, a = _agg(d["before"]), _agg(d["after"])
        flag = ("保护" if is_protected(ch) else "**可抑制**")
        print(f"  {ch:28s} ${b['net']:>9.2f} n={b['n']:<3} wr={b['wr']*100:>5.1f}%  "
              f"${a['net']:>9.2f} n={a['n']:<3} wr={a['wr']*100:>5.1f}%   {flag}")

    print("\nC. 熔断状态 × 通道（当前 shadow 的通道，after 侧是否已被抑制）")
    shadow_path = ROOT / "data" / "fusion_attribution.json"
    shadow = {}
    if shadow_path.exists():
        st = json.loads(shadow_path.read_text(encoding="utf-8"))
        shadow = {k for k, v in (st.get("breaker_shadow") or {}).items() if v}
    if not shadow:
        print("  （无 shadow 通道）")
    for key in sorted(shadow):
        tier, _, ch = key.partition("|")
        after_side = [x for x in items if x["channel"] == ch and x["tier"] == tier and x["side"] == "after"]
        print(f"  {key:34s} {'保护(永不抑制)' if is_protected(ch) else '可抑制':14s} "
              f"after 侧平仓 {len(after_side)} 笔")

    print("\nD. 浮盈回吐（按 peak_pnl_pct 分层，全部区间）")
    buckets = [("peak<2%", lambda p: p < 0.02), ("2%≤peak<5%", lambda p: 0.02 <= p < 0.05),
               ("peak≥5%", lambda p: p >= 0.05)]
    for name, f in buckets:
        sel = [x for x in items if x["peak"] is not None and f(x["peak"])]
        a = _agg(sel)
        give = [x["peak"] - (x["ret"] or 0) for x in sel if x["ret"] is not None]
        g = (sum(give) / len(give) * 100) if give else 0.0
        print(f"  {name:12s} n={a['n']:<4} 净额=${a['net']:>9.2f} 单笔=${a['avg']:>7.2f} "
              f"胜率={a['wr']*100:>5.1f}% 平均回吐={g:>5.2f}pt")

    print("\nE. 被通道熔断**抑制**的离场次数（0 = 本闸至今未动手）")
    print(f"  position_exit_events.exit_channel_broken = {suppressed}")
    print(f"  full_auto_sessions.event_log 内计数        = {sess_events}")

    # [§84 / 决策 P26-B] 自动复核条件：after 侧样本 ≥15 笔 或 首次出现抑制事件
    after_n = sum(1 for x in items if x["side"] == "after")
    need_n, have_sup = 15, int(suppressed or 0) + int(sess_events or 0)
    ready = (after_n >= need_n) or (have_sup > 0)
    print("\nE′. P26-B 复核条件（每日自动判定，用于决定是否重估 P19-B 抑制面）")
    print(f"  after 侧 mid/long 平仓 {after_n}/{need_n} 笔｜抑制事件 {have_sup} 次 "
          f"⇒ {'✅ 条件已满足：可以复核' if ready else '⏳ 未满足，继续等真实样本'}")

    print("\nF. 逐通道**浮盈回吐**（peak_pnl_pct − 最终收益率，pt；>0 = 把浮盈还回去）")
    print(f"  {'通道':28s} {'笔数':>5} {'回吐均值':>9} {'回吐最大':>9} {'净额$':>10} {'胜率':>7}")
    gb_rows = []
    for ch, d in sorted(byc.items()):
        sel = [x for x in d["before"] + d["after"]
               if x["peak"] is not None and x["ret"] is not None]
        if not sel:
            continue
        gives = [x["peak"] - x["ret"] for x in sel]
        a = _agg(sel)
        gb_rows.append((ch, len(sel), sum(gives) / len(gives) * 100, max(gives) * 100,
                        a["net"], a["wr"]))
    for ch, n, avg_g, max_g, net, wr in sorted(gb_rows, key=lambda r: -r[2]):
        print(f"  {ch:28s} {n:>5} {avg_g:>8.2f}pt {max_g:>8.2f}pt {net:>10.2f} {wr*100:>6.1f}%")
    worst = max(gb_rows, key=lambda r: r[2]) if gb_rows else None
    if worst:
        print(f"  ⇒ 回吐最严重通道：{worst[0]}（均值 {worst[2]:.2f}pt，最大 {worst[3]:.2f}pt，"
              f"净额 ${worst[4]:.2f}）")

    if args.json:
        out = ROOT / "data" / "audit_reports" / f"fix_effect_slice_{datetime.now():%Y%m%d_%H%M}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "boundary": args.boundary, "days": args.days,
            "totals": {s: _agg([x for x in items if x["side"] == s]) for s in ("before", "after")},
            "channels": {ch: {s: _agg(d[s]) for s in ("before", "after")} for ch, d in byc.items()},
            "suppressed_exit_events": int(suppressed or 0),
            "suppressed_session_events": int(sess_events or 0),
            "shadow_channels": sorted(shadow),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n快照：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
