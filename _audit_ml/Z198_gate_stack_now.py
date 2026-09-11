# -*- coding: utf-8 -*-
"""Z198：现在到底哪几道闸在拦中长线开仓（近 2h 逐条原因 + 参数现状）。

背景：2026-09-10 20:5x 实测中长线 **0 开仓**、3 笔持仓全部亏损平掉、权益 4707→4667，
回撤判据恶化到 23.60σ（age 1.1h ⇒ P17 stale 规则不会触发）。本脚本给出：
  1. 近 2h 每条拦截原因的**完整文本 + 计数**；
  2. `reentry_cooldown`（midlong_cooldown_block 的来源）的规则与参数；
  3. 当前生效的关键闸参数（dd / 并发 / 冷却 / EV / corr cluster）。
"""
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

since = time.time() - 2 * 3600
c: Counter[str] = Counter()
for r in _iter_rows(audit_paths()):
    if float(r.get("epoch") or 0) < since:
        continue
    if str(r.get("outcome")) != "skip":
        continue
    c[str(r.get("reason") or "")[:150]] += 1
print(f"=== 近 2h 拦截原因（{sum(c.values())} 条）===")
for k, v in c.most_common(12):
    print(f"  {v:5d}  {k}")

print("\n=== 冷却闸规则与参数 ===")
try:
    from backend.config import settings as S
    for k in ("REENTRY_COOLDOWN_SECONDS", "MIDLONG_INDEPENDENT_COOLDOWN_ENFORCE",
              "DIFF_COOLDOWN_SEC", "SCALP_COOLDOWN_SEC"):
        print(f"  {k} = {getattr(S, k, '<未声明>')}")
    from backend.services import reentry_cooldown as rc
    import inspect
    src = inspect.getsource(rc.reopen_blocked)
    print("  reopen_blocked 关键行:")
    for line in src.splitlines():
        s = line.strip()
        if any(x in s for x in ("cooldown", "mult", "loss", "reason", "settings.")):
            print("    ", s[:110])
except Exception as exc:  # noqa: BLE001
    print("  读取失败:", type(exc).__name__, str(exc)[:160])

print("\n=== 当前生效的闸参数 ===")
from backend.config import settings as S  # noqa: E402
from backend.services.risk_management import portfolio_budget as pbm  # noqa: E402

for k in ("MIDLONG_MAX_NET_EXPOSURE_PCT", "MIDLONG_MAX_OPEN_POSITIONS",
          "MIDLONG_MAX_SAME_SYMBOL_POSITIONS", "MIDLONG_CORR_CLUSTER_MAX",
          "MIDLONG_CORR_CLUSTER_SYMBOLS", "MIDLONG_PORTFOLIO_NOTIONAL_ALIGNED",
          "MIDLONG_EV_ENFORCE_MID", "MIDLONG_EV_GATE_ENABLED"):
    v = getattr(S, k, None)
    if v is None:
        v = os.getenv(k, "<未设>")
    print(f"  {k} = {v}")
print(f"  PB_MIDLONG_DRAWDOWN_SIGMA = {pbm._cfg_float('PB_MIDLONG_DRAWDOWN_SIGMA', 10.0)}")
print(f"  PB_DD_STALE_HOURS         = {pbm._cfg_float('PB_DD_STALE_HOURS', 12.0)}")
print(f"  PB_CONSEC_LOSS_LIMIT      = {pbm._cfg_int('PB_CONSEC_LOSS_LIMIT', 5)}")
