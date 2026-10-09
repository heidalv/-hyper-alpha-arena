"""轮47 验证器：手动触发一次**完整**（非 quick）15m 因子进化，验证 WFO_IC_MAX_P=0.15 的实际放行数。

- 与线上 cron 完全同一入口：run_factor_evolution_loop(period="15m", quick=False)
- 先打印生效阈值（证明读到了 .env 的 0.15），作为验证证据
- 结果（含 promoted 计数）打印到 stdout，由调用方重定向到日志
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 与后端启动同路径：确保 .env 已载入 os.environ
from backend.config.env_registry import ensure_env_loaded  # noqa: E402
ensure_env_loaded()

from backend.config.cycle_semantics import intraday_periods  # noqa: E402
from backend.services.evolution import factor_wfo as _fw  # noqa: E402

print("=" * 78, flush=True)
print(f"[VERIFY] 启动时刻 = {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
print(f"[VERIFY] WFO_IC_MAX_P        = {_fw._WFO_IC_MAX_P}   (env={os.getenv('WFO_IC_MAX_P')!r})", flush=True)
print(f"[VERIFY] WFO_IC_MIN_OOS_IC   = {_fw._WFO_IC_MIN_OOS_IC}", flush=True)
print(f"[VERIFY] WFO_IC_MAX_DECAY    = {_fw._WFO_IC_MAX_DECAY}", flush=True)
print(f"[VERIFY] 日内迁档周期集合     = {sorted(intraday_periods())} (空=未启用)", flush=True)
print(f"[VERIFY] FACTOR_EVO_WFO_MIN_SYMBOL_RATIO = "
      f"{os.getenv('FACTOR_EVO_WFO_MIN_SYMBOL_RATIO', '<默认0.66>')!r}", flush=True)
assert float(_fw._WFO_IC_MAX_P) == 0.15, f"阈值未生效: {_fw._WFO_IC_MAX_P}"
print("=" * 78, flush=True)

from backend.services.evolution.factor_evolution_loop import run_factor_evolution_loop  # noqa: E402

t0 = time.time()
report = run_factor_evolution_loop(period="15m", quick=False, source="manual_verify_r47")
el = time.time() - t0

print("=" * 78, flush=True)
print(f"[VERIFY] 完成，用时 {el:.0f}s", flush=True)
print("[VERIFY] report = " + json.dumps(report, ensure_ascii=False, default=str)[:1200], flush=True)
for k in ("candidates", "evaluated", "survivors", "promoted", "advanced", "degraded", "replaced", "active_total"):
    print(f"[VERIFY]   {k} = {report.get(k)}", flush=True)
print("=" * 78, flush=True)
