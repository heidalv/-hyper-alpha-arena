# -*- coding: utf-8 -*-
"""[§92 核查 2026-09-11] 套件里的**账户口径**：4 个基于持仓的审计脚本是否被测试账户污染？

§90.4 定的规则是"业绩口径一律 account_id=14"。但每日套件里的脚本：
  * `audit_profit_giveback.py`（浮盈回吐）
  * `audit_gate_edge.py`（闸门边际效益）
  * `audit_position_event_consistency.py`（持仓/事件一致性）
  * `audit_paper_live_divergence.py`（paper/live 分叉）
源码里 `account_id` 命中 0 次 ⇒ 很可能把 #147/#149/#156 的测试仓位也算进"业绩"。

本脚本对每个脚本做**同口径对照**：直接连库跑"全账户 vs 仅 14"的同类聚合，
量化差异（无法自动改写脚本 SQL，故以最接近的聚合近似），并打印判定。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from _scope import DEFAULT_ACCOUNT_ID, describe_scope  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select account_id, lower(coalesce(timeframe_tier,'?')),
                   coalesce(peak_pnl_pct,0), {NET} as net
            from paper_positions
            where status='closed' and closed_at >= now() - interval '{days} day'
              and lower(coalesce(timeframe_tier,'')) in ('mid','long')
        """)).fetchall()
    finally:
        db.close()

    print(describe_scope())
    print("=" * 96)
    print(f"套件脚本的账户口径对照（近 {days} 天 mid/long；giveback 模式 = peak≥2% 且最终为负）")
    print("=" * 96)
    for label, pred in (("全账户（当前脚本口径）", lambda a: True),
                        (f"仅账户 {DEFAULT_ACCOUNT_ID}", lambda a: int(a) == DEFAULT_ACCOUNT_ID)):
        sel = [r for r in rows if pred(r[0])]
        n = len(sel)
        if not n:
            print(f"  {label:20s} 无样本")
            continue
        net = sum(float(r[3] or 0) for r in sel)
        mode = [r for r in sel if float(r[2] or 0) >= 0.02 and float(r[3] or 0) < 0]
        big = [r for r in sel if float(r[3] or 0) <= -0.02]
        print(f"  {label:20s} n={n:>3} 净额 {net:>9.2f}｜giveback 模式 {len(mode):>2} 笔"
              f"（{100.0*len(mode)/n:>5.1f}%）｜大亏 {len(big):>2} 笔")
        for tier in ("mid", "long"):
            ts = [r for r in sel if r[1] == tier]
            if ts:
                print(f"      {tier:5s} n={len(ts):>3} 净额 {sum(float(r[3] or 0) for r in ts):>9.2f}")

    print("\n【受影响脚本（源码无 account_id ⇒ 走全账户口径）】")
    for f in ("audit_profit_giveback.py", "audit_gate_edge.py",
              "audit_position_event_consistency.py", "audit_paper_live_divergence.py"):
        p = ROOT / "backend/scripts" / f
        src = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        has = ("account_id" in src)
        print(f"  {f:44s} {'✅ 已按账户' if has else '❗ 未按账户（口径混杂）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
