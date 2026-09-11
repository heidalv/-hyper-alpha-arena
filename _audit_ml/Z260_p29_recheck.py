# -*- coding: utf-8 -*-
"""[§89 观察 2026-09-11 / 决策 P29-C] long 层 SL 上限 + 风险预算的**效果复核器**（只读）。

部署边界默认 **2026-09-11 17:20**（P29-C 上线）。判定口径：

  就绪条件（先到为准）：① 边界后有 **≥5 笔 long 平仓**；② 或日志里出现
  `[Paper] SL 距离超上限被拉近`（证明夹子在生产路径真的生效）。
  未就绪 ⇒ 打印 NOT_READY（不假装有结论）。

  就绪后给出对照：SL 距离 / `sl` 通道占比 / 单笔亏损中位 / MAE 是否接近 SL。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _scope import account_clause, describe_scope  # noqa: E402

ACCT = account_clause()   # [§90/#73] 账户隔离（默认 account_id=14，AUDIT_ACCOUNT_ID=0 关闭）

from backend.database.connection import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

BOUNDARY = os.environ.get("P29_BOUNDARY", "2026-09-11 17:20")
NEED_CLOSES = int(os.environ.get("P29_NEED_CLOSES", "5"))
NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")


def readiness(n_after: int, clamp_logged: bool, *, need: int = NEED_CLOSES) -> Dict[str, object]:
    """纯函数：是否已具备复核条件（可测）。"""
    ready = (n_after >= need) or clamp_logged
    why = []
    if n_after >= need:
        why.append(f"边界后 long 平仓 {n_after}≥{need}")
    if clamp_logged:
        why.append("已见夹子日志")
    return {"ready": ready, "n_after": n_after, "need": need,
            "reason": "；".join(why) if why else f"边界后 long 平仓 {n_after}/{need}，未见夹子日志"}


def _rows(db, since: Optional[str]):
    where = f"and closed_at >= timestamp '{since}'" if since else \
            f"and closed_at < timestamp '{BOUNDARY}'"
    return db.execute(text(f"""
        select upper(symbol), coalesce(close_reason,'?'), {NET} as net,
               entry_price, sl_price, coalesce(trough_pnl_pct,0), coalesce(peak_pnl_pct,0)
        from paper_positions
        where status='closed'{ACCT} and lower(coalesce(timeframe_tier,''))='long'
          and entry_price > 0 and close_price is not null {where}
        order by closed_at
    """)).fetchall()


def _stats(rows) -> Dict[str, float]:
    if not rows:
        return {"n": 0}
    dists = [abs(float(r[3]) - float(r[4])) / float(r[3]) * 100 for r in rows if r[4]]
    losses = [float(r[2]) for r in rows if float(r[2]) < 0]
    return {
        "n": len(rows),
        "sl_dist_med": sorted(dists)[len(dists) // 2] if dists else 0.0,
        "sl_share": sum(1 for r in rows if "sl" == str(r[1]).split(":")[0].strip().lower()) / len(rows),
        "avg_net": sum(float(r[2]) for r in rows) / len(rows),
        "med_loss": (sorted(losses)[len(losses) // 2] if losses else 0.0),
        "mae_med": (sorted(float(r[5]) for r in rows)[len(rows) // 2] * 100),
    }


def main() -> int:
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        after = _rows(db, BOUNDARY)
        before = _rows(db, None)
    finally:
        db.close()

    log = ROOT / "logs" / "backend.log"
    clamp_logged = False
    if log.exists():
        try:
            clamp_logged = "SL 距离超上限被拉近" in log.read_text(encoding="utf-8", errors="replace")
        except Exception:
            clamp_logged = False

    r = readiness(len(after), clamp_logged)
    print(describe_scope())
    print("=" * 100)
    print(f"P29-C 效果复核器｜边界 {BOUNDARY}｜就绪条件：≥{NEED_CLOSES} 笔 long 平仓或见夹子日志")
    print("=" * 100)
    print(f"判定：**{'READY（可以复核）' if r['ready'] else 'NOT_READY（继续等）'}** — {r['reason']}")
    sa, sb = _stats(after), _stats(before)
    print(f"\n  {'窗口':16s} {'n':>4} {'SL距离中位%':>12} {'sl通道占比':>11} {'单笔净额$':>10} "
          f"{'亏损中位$':>10} {'MAE中位%':>9}")
    for label, s in ((f"边界前(<{BOUNDARY[5:]})", sb), ("边界后", sa)):
        if not s.get("n"):
            print(f"  {label:16s} {0:>4}  （无样本）")
            continue
        print(f"  {label:16s} {s['n']:>4} {s['sl_dist_med']:>12.2f} {s['sl_share']*100:>10.1f}% "
              f"{s['avg_net']:>10.2f} {s['med_loss']:>10.2f} {s['mae_med']:>9.2f}")
    print("\n  ⇒ 复核要点：SL 距离中位是否降到 ≤3%？`sl` 通道占比是否上升（0/34 → >0）？"
          "\n     单笔亏损中位是否收窄（目标 −$19 → −$5~−$7 量级）？")
    if not r["ready"]:
        print("  ⚠️ 未就绪时**不下结论**（避免用 1–2 笔样本说话）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
