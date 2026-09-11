# -*- coding: utf-8 -*-
"""Z183：`portfolio_budget_block` 到底在拦什么（近 3h 全文 + 运行期状态）。"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from backend.services.mlto.midlong_direction_audit import audit_paths, _iter_rows  # noqa: E402

since = time.time() - 3 * 3600
cnt: Counter[str] = Counter()
first_ts = None
last_ts = None
for r in _iter_rows(audit_paths()):
    ep = float(r.get("epoch") or 0)
    if ep < since:
        continue
    reason = str(r.get("reason") or "")
    if "portfolio_budget_block" not in reason:
        continue
    cnt[reason[:160]] += 1
    if first_ts is None or ep < first_ts:
        first_ts = ep
    if last_ts is None or ep > last_ts:
        last_ts = ep

print(f"近 3h `portfolio_budget_block` 共 {sum(cnt.values())} 条")
if first_ts:
    print("  首次:", time.strftime("%H:%M:%S", time.localtime(first_ts)),
          "| 最近:", time.strftime("%H:%M:%S", time.localtime(last_ts)))
for k, v in cnt.most_common(10):
    print(f"  {v:5d}  {k}")

print("\n=== 运行期：portfolio_budget 的判据与当前值 ===")
try:
    from backend.services.risk_management import portfolio_budget as pb_mod

    pb = pb_mod.portfolio_budget
    print("  实例:", type(pb).__name__)
    for attr in dir(pb):
        if attr.startswith("_"):
            continue
        try:
            v = getattr(pb, attr)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(v, (int, float, str, bool)) or v is None:
            print(f"    {attr} = {v!r}")
    # 直接问一次（用一个假的开仓请求，看它给什么理由）
    from backend.database.connection import SessionLocal

    db = SessionLocal()
    try:
        dec = pb.evaluate_open(
            symbol="SOL", action="buy", notional_usd=800.0, equity=4717.0,
            strategy="midlong", mode="paper", db=db, account_id=14, positions=None,
        )
        print("  试算 SOL buy $800 →", "允许" if dec.allowed else "拦截", "| reasons:", dec.reasons[:4])
    finally:
        db.close()
except Exception as exc:  # noqa: BLE001
    print("  探测失败:", type(exc).__name__, str(exc)[:200])
