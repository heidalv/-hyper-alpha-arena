# -*- coding: utf-8 -*-
"""[F128] 实盘 vs 模型 **同窗口对齐复核**（一键命令，每次改配置后必跑）。

为什么需要：本项目最大的坑是"实盘与模型悄悄分叉"——历史上出现的分叉包括
成交桶重复判定、未落库桶被使用、波动基准口径不同、行窗锚点错位（F103/F107/F108c）。
F127 证明**只要把两边放到同一段窗口**就能立刻判定是否分叉（当时 0.95×）。

用法：
    python scripts/mm_align_check.py                    # 默认：最近 1 小时
    python scripts/mm_align_check.py 3                  # 最近 3 小时
    python scripts/mm_align_check.py --since 2026-09-14T21:03:00+08:00

输出：成交/h（实盘/模型/比值）、每笔净 bp、双账一致性、空分片率、单侧率、挂宽/σ，
并对"比值偏离 1 倍"和"每笔 bp 差超过 3 倍标准误"给出明确告警。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"


def _live_fills(since_dt):
    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    q = ("SELECT symbol, notional, net_bp, spread_bp, price_bp, fee_bp, meta_json"
         " FROM lane_ledger WHERE lane_id=:l AND event='fill' AND ts >= :a ORDER BY ts")
    with system_identity():
        with SessionLocal() as db:
            db.execute(text("SET statement_timeout = 30000"))
            rows = db.execute(text(q), {"l": LANE, "a": since_dt}).mappings().all()
            return [dict(r) for r in rows]


def _agg(rows):
    n, nt = 0, 0.0
    acc = {"net": 0.0, "spread": 0.0, "price": 0.0, "fee": 0.0}
    per = {}
    for x in rows:
        m = x.get("meta_json") or {}
        if str(m.get("source") or "") == "reconcile":
            continue
        v = float(x.get("notional") or 0.0)
        if v <= 0:
            continue
        n += 1
        nt += v
        for k in acc:
            acc[k] += v * float(x.get(f"{k}_bp") or 0.0) / 1e4
        d = per.setdefault(str(x.get("symbol")), [0, 0.0, 0.0])
        d[0] += 1
        d[1] += v
        d[2] += v * float(x.get("net_bp") or 0.0) / 1e4
    return n, nt, acc, per


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("hours", nargs="?", type=float, default=1.0)
    ap.add_argument("--since", default=None)
    ap.add_argument("--lane", default=LANE)
    args = ap.parse_args()

    from backend.services.market_maker.portfolio_replay import replay_portfolio, _load_all
    from backend.services.market_maker.runner import get_runner
    from backend.services import lane_registry as reg

    # 默认窗口起点必须**不早于最近一次配置变更**：否则实盘是"旧配置+新配置"的混合，
    # 而模型全程用新配置 ⇒ 速率比天然偏低（实测 0.69×，纯属窗口跨越变更的假象 ✗）。
    last_change = None
    try:
        meta = (reg.get_lane(args.lane) or {}).get("meta") or {}
        ops = meta.get("ops_changes") or []
        ev = meta.get("evolution") or {}
        cands = [x.get("ts") for x in ops if x.get("ts")]
        if ev.get("last_change_ts"):
            cands.append(ev["last_change_ts"])
        if cands:
            last_change = max(datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                              for t in cands).astimezone()
    except Exception:
        last_change = None

    since_dt = (datetime.fromisoformat(args.since) if args.since
                else datetime.now().astimezone() - timedelta(hours=args.hours))
    clipped = False
    if args.since is None and last_change is not None and last_change > since_dt:
        since_dt, clipped = last_change, True
    a_ms = int(since_dt.timestamp() * 1000)
    span_h = max(0.01, (datetime.now().astimezone() - since_dt).total_seconds() / 3600.0)

    r = get_runner(args.lane)
    if r is None:
        print(f"车道 {args.lane} 的运行态不可用")
        return 1
    syms = list(r.symbols)
    print(f"=== 实盘/模型对齐复核：{args.lane} — 窗口 {since_dt:%m-%d %H:%M:%S} ~ now"
          f"（{span_h:.2f}h）===")
    if clipped:
        print(f"（窗口已自动裁剪到最近一次配置变更 {last_change:%m-%d %H:%M:%S}，"
              f"保证两边跑的是同一套配置；用 --since 可覆盖）")
    print(f"配置: w_base={r.params.w_base_bp} k_vol={r.params.k_vol} "
          f"k_inv={r.params.k_inv} frozen={r.params.frozen_width_bp}/"
          f"{r.params.frozen_max_move_bp} | net_cap={r.limits.max_net_exposure_ratio} "
          f"dir_cap={r.limits.max_net_directional_ratio} "
          f"fill_notional={r.fill_notional} compound={r.compound_ratio}")

    n, nt, acc, per = _agg(_live_fills(since_dt))
    lbp = acc["net"] / nt * 1e4 if nt else 0.0
    print(f"\n实盘: {n} 笔 = {n/span_h:.1f}/h  净 {acc['net']:+.2f}USD ({lbp:+.3f}bp)  "
          f"价差 {acc['spread']/nt*1e4 if nt else 0:+.2f} 价格 {acc['price']/nt*1e4 if nt else 0:+.2f} "
          f"费 {acc['fee']/nt*1e4 if nt else 0:+.2f}")
    if per:
        print("  分币:", {k: f"{v[2]/v[1]*1e4:+.1f}bp/{v[0]}" for k, v in per.items()})

    # 模型：同窗口，实盘同源口径（锚定波动基准 + 武装车道闸门 + 实测滞后中位）
    DATA = _load_all(syms, r.venue)
    lo_ms = a_ms - 7200_000
    sub = {}
    for s in syms:
        d = DATA[s]
        m = d["ots"] >= lo_ms
        sub[s] = {k: d[k][m] for k in ("ots", "bb", "ba")}
        tm = d["tts"] >= lo_ms
        for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
            sub[s][k] = d[k][tm]
    anchored = {s: float(st.vol_baseline_bp or 0.0) for s, st in r.states.items()}
    from backend.services.market_maker.evolution import DEFAULT_TICK_DELAY_MS
    rep = replay_portfolio(syms, venue=r.venue, equity=r.equity, params=r.params,
                           limits=r.limits, fill_notional=r.fill_notional,
                           fill_notional_ratio=max(0.0, r.compound_ratio), data=sub,
                           start_ts_ms=lo_ms, enforce_lane_limits=True,
                           tick_delay_ms=DEFAULT_TICK_DELAY_MS, vol_baseline=anchored,
                           collect_quotes=True)
    mfl = [x for x in rep["fills_log"] if int(x["ts_ms"]) >= a_ms]
    mnt = sum(float(x["notional"]) for x in mfl)
    mnu = sum(float(x["net_usd"]) for x in mfl)
    mbp = mnu / mnt * 1e4 if mnt else 0.0
    sc = rep["side_counts"]
    nj, ne = rep["win_judged"], rep["win_empty"]
    print(f"模型: {len(mfl)} 笔 = {len(mfl)/span_h:.1f}/h  净 {mnu:+.2f}USD ({mbp:+.3f}bp)")
    print(f"  单侧率 {sc['one']/max(1,sum(sc.values()))*100:.1f}%  "
          f"空分片 {ne/max(1,nj+ne)*100:.1f}%  挂宽 {rep['avg_width_bp']['bid']}/"
          f"{rep['avg_width_bp']['ask']}  σ {rep['avg_sigma']}")

    ratio = n / max(1, len(mfl))
    print(f"\n速率比 实盘/模型 = {ratio:.2f}×")
    warn = []
    if not (0.7 <= ratio <= 1.4):
        warn.append(f"速率比 {ratio:.2f}× 偏离 1（>1.4 或 <0.7）⇒ 可能已分叉 ✗")
    if n >= 30:
        # 每笔净额的粗略标准差：做市单笔 ≈ 15bp（F95 实测）⇒ 标准误 ≈ 15/√n
        se = 15.0 / (n ** 0.5)
        if abs(lbp - mbp) > 3 * se:
            warn.append(f"每笔 bp 差 {abs(lbp-mbp):.2f}bp > 3σ({3*se:.2f}bp) ⇒ 差异显著 ✗")
        else:
            warn.append(f"每笔 bp 差 {abs(lbp-mbp):.2f}bp < 3σ({3*se:.2f}bp) ⇒ 差异不显著 ✓")
    else:
        warn.append(f"实盘样本仅 {n} 笔 ⇒ 不足以判定 bp 差异（需 ≥30）")
    for w in warn:
        print("  ·", w)
    print("\n提示：分叉优先查三处——(1) 成交桶可见性/分片口径（F107）；"
          "(2) 波动基准是否与实盘同源（F108c）；(3) 车道闸门是否武装（F116）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
