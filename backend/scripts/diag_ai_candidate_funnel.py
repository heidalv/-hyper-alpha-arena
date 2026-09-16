# -*- coding: utf-8 -*-
"""[调研轮16 2026-09-16] **AI 选币候选的落地漏斗**只读诊断。

背景：AI 选币链路（`coin_select_candidates` → `_midlong_board_approve_candidates` →
扫描批次 → 论题 → 开仓）确认已通到"候选进入扫描"，但近 7 天 `paper_positions.entry_source`
**没有任何 auto_coin 来源的开仓**。要回答的不是"链路通不通"，而是
**候选究竟死在哪一道闸**。

做法：把两侧数据交叉 ——
  A. DB：`coin_select_adoptions` / `coin_select_candidates`（AI 采纳/过审的 symbol）；
  B. 审计 JSONL：`data/midlong_direction_audit.jsonl*`（每次开/拒仓一行，含
     outcome/stage/reason/hub_action/score），以及 `paper_positions.entry_source`。

完全只读。用法：
  python backend/scripts/diag_ai_candidate_funnel.py --days 7
  python backend/scripts/diag_ai_candidate_funnel.py --days 7 --session fa_7e12e7a1b6
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--session", default="fa_7e12e7a1b6")
    ap.add_argument("--detail", type=int, default=18)
    args = ap.parse_args()

    import time

    from backend.services.mlto.midlong_direction_audit import audit_paths, _iter_rows

    cutoff = time.time() - args.days * 86400.0

    # ── A. AI 侧：采纳 / 过审 symbol ──────────────────────────────
    adopted: dict[str, set[str]] = defaultdict(set)
    approved: dict[str, set[str]] = defaultdict(set)
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import (
            CoinSelectAdoption, CoinSelectCandidate,
        )
        from datetime import datetime, timedelta, timezone

        since = datetime.now(timezone.utc) - timedelta(days=args.days)
        db = SessionLocal()
        try:
            for a in db.query(CoinSelectAdoption).filter(
                CoinSelectAdoption.session_id == args.session,
                CoinSelectAdoption.created_at >= since,
            ).all():
                adopted[(a.symbol or "").upper()].add((a.horizon or "?").lower())
            for c in db.query(CoinSelectCandidate).filter(
                CoinSelectCandidate.created_at >= since,
            ).all():
                sym = (getattr(c, "symbol", "") or "").upper()
                if (getattr(c, "ai_verdict", "") or "").lower() in ("approve", "watch"):
                    approved[sym].add((getattr(c, "horizon", "") or "?").lower())
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] DB 侧读取失败（只看审计侧）: {type(exc).__name__}: {exc}")

    print(f"== A. AI 侧（近 {args.days:g} 天, session={args.session}）==")
    print(f"采纳(adoptions) {len(adopted)} 个: "
          f"{sorted(f'{s}[{"/".join(sorted(h))}]' for s, h in adopted.items())}")
    ai_syms = set(adopted) | set(approved)

    # ── B. 审计侧 ────────────────────────────────────────────────
    paths = audit_paths()
    print(f"\n== B. 审计 JSONL: {len(paths)} 个文件（含轮转）==")
    rows = []
    for r in _iter_rows(paths):
        try:
            ep = float(r.get("epoch") or 0)
        except (TypeError, ValueError):
            ep = 0.0
        if ep < cutoff:
            continue
        if args.session and str(r.get("session_id") or "") != args.session:
            continue
        rows.append(r)
    print(f"近 {args.days:g} 天行数: {len(rows)}")

    by_out = Counter(str(r.get("outcome") or "?") for r in rows)
    print(f"outcome 分布: {dict(by_out)}")

    # 来源分布 × outcome
    src_out: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        src_out[str(r.get("source") or "-")][str(r.get("outcome") or "?")] += 1
    print("source × outcome:")
    for s, c in sorted(src_out.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"   {s:<14}{dict(c)}")

    # AI 候选 symbol 的死因
    print(f"\n== C. AI 候选在审计里的去向（{len(ai_syms)} 个 symbol）==")
    if not ai_syms:
        print("  （A 段无数据；改用 --session '' 看全量）")
    per_sym: dict[str, Counter] = defaultdict(Counter)
    reason_sym: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        sym = str(r.get("symbol") or "").upper()
        if sym not in ai_syms:
            continue
        per_sym[sym][f"{r.get('outcome')}@{r.get('stage')}"] += 1
        if str(r.get("outcome")) == "skip":
            reason_sym[sym][str(r.get("reason") or "-").split(":")[0][:46]] += 1
    for sym in sorted(ai_syms):
        c = per_sym.get(sym)
        if not c:
            print(f"   {sym:<9} 审计里**零行**（没进过中长线决策漏斗）")
            continue
        top = ", ".join(f"{k}×{v}" for k, v in reason_sym[sym].most_common(4))
        print(f"   {sym:<9} {dict(c)}")
        if top:
            print(f"            主要拦截: {top}")

    # ── D. 明细 + 全量 opened 对照 ───────────────────────────────
    if args.detail and ai_syms:
        print(f"\n== D. AI 候选最近 {args.detail} 行明细 ==")
        hits = [r for r in rows if str(r.get("symbol") or "").upper() in ai_syms]
        for r in hits[-args.detail:]:
            ts = str(r.get("ts") or "")[5:19].replace("T", " ")
            print(f"   {ts} {str(r.get('symbol')):<7} {str(r.get('outcome')):<12}"
                  f"{str(r.get('stage')):<7}{str(r.get('tier') or '-'):<5}"
                  f"{str(r.get('source') or '-'):<13}{str(r.get('action') or '-'):<5}"
                  f"score={str(r.get('score') or '-'):<5} {str(r.get('reason') or '')[:78]}")

    opened = [r for r in rows if str(r.get("outcome")) == "opened"]
    print(f"\n== E. 近 {args.days:g} 天 opened 行: {len(opened)} ==")
    for r in opened[-12:]:
        ts = str(r.get("ts") or "")[5:19].replace("T", " ")
        print(f"   {ts} {str(r.get('symbol')):<7}{str(r.get('tier') or '-'):<5}"
              f"{str(r.get('source') or '-'):<13}{str(r.get('reason') or '')[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
